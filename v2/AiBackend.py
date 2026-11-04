#!/usr/bin/env python3
"""Local backend process for the Quickshell AI widget."""

import json
import base64
import os
import re
import pty
import select
import shutil
import signal
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request


output_lock = threading.Lock()
active_lock = threading.Lock()
active = {}
recordings = {}
recordings_lock = threading.Lock()
agent_lock = threading.Lock()

def emit(request_id, event, **values):
    message = {"id": request_id, "event": event, **values}
    with output_lock:
        sys.stdout.write(json.dumps(message, ensure_ascii=True, separators=(",", ":")) + "\n")
        sys.stdout.flush()


def request_json(url, payload, timeout=120):
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    return urllib.request.urlopen(request, timeout=timeout)


def handle_http(request_id, message):
    try:
        url = str(message.get("url", ""))
        method = str(message.get("method", "GET")).upper()
        payload = message.get("payload")
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8") if payload is not None else None
        headers = {"Content-Type": "application/json"} if data is not None else {}
        request = urllib.request.Request(url, data=data, headers=headers, method=method)
        with urllib.request.urlopen(request, timeout=max(1, min(int(message.get("timeout", 90)), 180))) as response:
            text = response.read(2_000_000).decode("utf-8", errors="replace")
        emit(request_id, "http_finished", text=text)
    except Exception as error:
        emit(request_id, "http_finished", text="", error=str(error))


def handle_file(request_id, message):
    operation = str(message.get("operation", ""))
    try:
        if operation == "read_many":
            contents = []
            for path in message.get("paths", []):
                try:
                    with open(path, "r", encoding="utf-8") as state_file:
                        contents.append(state_file.read())
                except OSError:
                    contents.append("")
            emit(request_id, "file_finished", contents=contents)
        elif operation in ("write", "append_trim"):
            path = str(message.get("path", ""))
            os.makedirs(os.path.dirname(path), mode=0o700, exist_ok=True)
            content = str(message.get("content", ""))
            if operation == "append_trim":
                try:
                    with open(path, "r", encoding="utf-8") as state_file:
                        previous = state_file.read()
                except OSError:
                    previous = ""
                content = "\n".join((previous + ("\n" if previous else "") + content).splitlines()[-200:]) + "\n"
            with open(path, "w", encoding="utf-8") as state_file:
                os.chmod(path, 0o600)
                state_file.write(content)
            emit(request_id, "file_finished")
        elif operation == "delete":
            try:
                os.unlink(str(message.get("path", "")))
            except FileNotFoundError:
                pass
            emit(request_id, "file_finished")
        elif operation == "open":
            path = str(message.get("path", ""))
            os.makedirs(os.path.dirname(path), mode=0o700, exist_ok=True)
            open(path, "a", encoding="utf-8").close()
            subprocess.Popen(["xdg-open", path], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            emit(request_id, "file_finished")
        else:
            raise ValueError("unknown file operation")
    except Exception as error:
        emit(request_id, "file_finished", error=str(error))


def handle_chat(request_id, message):
    cancel = threading.Event()
    with active_lock:
        active[request_id] = cancel
    try:
        host = str(message.get("host", "http://127.0.0.1:11434")).rstrip("/")
        payload = message.get("payload")
        if not isinstance(payload, dict):
            raise ValueError("chat payload must be an object")
        image_path = message.get("image_path")
        if image_path:
            try:
                with open(image_path, "rb") as image_file:
                    encoded_image = base64.b64encode(image_file.read()).decode("ascii")
                for chat_message in payload.get("messages", []):
                    images = chat_message.get("images")
                    if images:
                        chat_message["images"] = [
                            encoded_image if image == "@@SHOT@@" else image for image in images
                        ]
            except OSError:
                pass
        with request_json(host + "/api/chat", payload, timeout=180) as response:
            for raw_line in response:
                if cancel.is_set():
                    break
                line = raw_line.decode("utf-8", errors="replace").strip()
                if not line:
                    continue
                try:
                    chunk = json.loads(line)
                except json.JSONDecodeError:
                    continue
                emit(request_id, "chat_chunk", chunk=chunk)
                if chunk.get("done"):
                    break
    except urllib.error.URLError as error:
        emit(request_id, "error", message="Can't reach Ollama: " + str(error.reason))
    except Exception as error:
        emit(request_id, "error", message=str(error))
    finally:
        with active_lock:
            active.pop(request_id, None)
        emit(request_id, "chat_finished", cancelled=cancel.is_set())


def handle_command(request_id, message):
    cancel = threading.Event()
    process = None
    with active_lock:
        active[request_id] = (cancel, None)
    try:
        command = str(message.get("command", "")).strip()
        if not command:
            raise ValueError("command is empty")
        timeout = max(1, min(int(message.get("timeout", 30)), 300))
        process = subprocess.Popen(
            ["bash", "-c", "exec 0</dev/null 2>&1\n(" + command + ")\necho \"[exit code: $?]\""],
            cwd=os.path.expanduser("~"),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
        with active_lock:
            active[request_id] = (cancel, process)
        try:
            output, _ = process.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            process.kill()
            output, _ = process.communicate()
            output += "\n[no exit code: command timed out after " + str(timeout) + "s or was killed]"
        emit(request_id, "command_finished", output=output, returncode=process.returncode)
    except Exception as error:
        emit(request_id, "error", message=str(error))
        emit(request_id, "command_finished", output="[command failed]", returncode=-1)
    finally:
        with active_lock:
            active.pop(request_id, None)


def build_ssh_argv(message):
    host = str(message.get("host", "")).strip()
    user = str(message.get("user", "")).strip()
    command = str(message.get("command", "")).strip()
    try:
        port = int(message.get("port", 22))
    except (TypeError, ValueError):
        raise ValueError("SSH port must be a number")
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,252}", host):
        raise ValueError("SSH host must be a hostname or IP address")
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_.-]{0,31}", user):
        raise ValueError("SSH username is invalid")
    if user.lower() == "root":
        raise ValueError("remote SSH requires a non-root account")
    if not 1 <= port <= 65535:
        raise ValueError("SSH port must be between 1 and 65535")
    if not command:
        raise ValueError("remote command is empty")

    ssh = shutil.which("ssh")
    if not ssh:
        raise FileNotFoundError("ssh client is not installed")
    argv = [
        ssh, "-T", "-x",
        "-o", "BatchMode=yes",
        "-o", "PreferredAuthentications=publickey",
        "-o", "StrictHostKeyChecking=yes",
        "-o", "ForwardAgent=no",
        "-o", "ClearAllForwardings=yes",
        "-o", "ControlMaster=no",
        "-o", "ControlPath=none",
        "-o", "PermitLocalCommand=no",
        "-o", "ConnectTimeout=10",
        "-p", str(port),
    ]
    identity_file = str(message.get("identity_file", "")).strip()
    if identity_file:
        identity_file = os.path.expanduser(identity_file)
        if not os.path.isfile(identity_file):
            raise FileNotFoundError("SSH identity file does not exist")
        argv.extend(["-i", identity_file, "-o", "IdentitiesOnly=yes"])
    argv.extend([user + "@" + host, command])
    return argv


def ensure_ssh_agent():
    with agent_lock:
        ssh_add = shutil.which("ssh-add")
        ssh_agent = shutil.which("ssh-agent")
        if not ssh_add or not ssh_agent:
            raise FileNotFoundError("OpenSSH client tools (ssh and ssh-add) are required")

        def agent_reachable(socket_path):
            if not socket_path:
                return False
            env = os.environ.copy()
            env["SSH_AUTH_SOCK"] = socket_path
            try:
                result = subprocess.run([ssh_add, "-l"], env=env, stdin=subprocess.DEVNULL,
                                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                        timeout=4, check=False)
                return result.returncode in (0, 1)
            except (OSError, subprocess.TimeoutExpired):
                return False

        current_socket = os.environ.get("SSH_AUTH_SOCK", "")
        if agent_reachable(current_socket):
            return current_socket

        runtime_dir = os.environ.get("XDG_RUNTIME_DIR") or "/tmp"
        socket_dir = os.path.join(runtime_dir, "ai-widget")
        os.makedirs(socket_dir, mode=0o700, exist_ok=True)
        try:
            os.chmod(socket_dir, 0o700)
        except OSError:
            pass
        socket_path = os.path.join(socket_dir, "ssh-agent.sock")
        if agent_reachable(socket_path):
            os.environ["SSH_AUTH_SOCK"] = socket_path
            return socket_path
        try:
            os.unlink(socket_path)
        except FileNotFoundError:
            pass

        result = subprocess.run(
            [ssh_agent, "-s", "-a", socket_path, "-t", "8h"],
            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, timeout=8, check=False,
        )
        match = re.search(r"SSH_AUTH_SOCK=([^;]+);", result.stdout)
        if result.returncode != 0 or not match or not agent_reachable(socket_path):
            raise RuntimeError("could not start ssh-agent")
        os.environ["SSH_AUTH_SOCK"] = match.group(1)
        pid_match = re.search(r"SSH_AGENT_PID=(\d+);", result.stdout)
        if pid_match:
            os.environ["SSH_AGENT_PID"] = pid_match.group(1)
        return match.group(1)


def ssh_agent_has_keys():
    ssh_add = shutil.which("ssh-add")
    if not ssh_add:
        return False
    result = subprocess.run([ssh_add, "-l"], stdin=subprocess.DEVNULL,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                            timeout=4, check=False)
    return result.returncode == 0


def add_ssh_key(passphrase, identity_file=""):
    ssh_add = shutil.which("ssh-add")
    if not ssh_add:
        raise FileNotFoundError("ssh-add is not installed")
    args = [ssh_add]
    identity_file = os.path.expanduser(str(identity_file).strip())
    if identity_file:
        if not os.path.isfile(identity_file):
            raise FileNotFoundError("SSH identity file does not exist")
        args.append(identity_file)

    child_pid, terminal_fd = pty.fork()
    if child_pid == 0:
        try:
            environment = os.environ.copy()
            environment["LC_ALL"] = "C"
            os.execvpe(ssh_add, args, environment)
        except OSError:
            os._exit(127)

    buffer = b""
    passphrase_sent = False
    start = time.monotonic()
    status = None
    try:
        while time.monotonic() - start < 35:
            readable, _, _ = select.select([terminal_fd], [], [], 0.1)
            if readable:
                try:
                    data = os.read(terminal_fd, 4096)
                except OSError:
                    break
                if not data:
                    break
                buffer = (buffer + data)[-8192:]
                prompt = buffer.lower()
                if not passphrase_sent and b"passphrase" in prompt:
                    os.write(terminal_fd, str(passphrase).encode("utf-8") + b"\n")
                    passphrase_sent = True
                    buffer = b""
                elif passphrase_sent and b"bad passphrase" in prompt:
                    os.write(terminal_fd, b"\x03")
                    break

            waited, child_status = os.waitpid(child_pid, os.WNOHANG)
            if waited == child_pid:
                status = child_status
                break
        if status is None:
            try:
                os.kill(child_pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            _, status = os.waitpid(child_pid, 0)
    finally:
        os.close(terminal_fd)

    return os.waitstatus_to_exitcode(status) == 0


def handle_agent_start(request_id, message):
    try:
        ensure_ssh_agent()
        emit(request_id, "agent_ready", has_keys=ssh_agent_has_keys())
    except Exception as error:
        emit(request_id, "agent_error", message=str(error))


def handle_agent_unlock(request_id, message):
    passphrase = str(message.pop("passphrase", ""))
    try:
        ensure_ssh_agent()
        loaded = add_ssh_key(passphrase, str(message.get("identity_file", "")))
        emit(request_id, "agent_unlock_finished", loaded=loaded,
             message="" if loaded else "Could not unlock the SSH key; check the passphrase and key path.")
    except Exception as error:
        emit(request_id, "agent_unlock_finished", loaded=False, message=str(error))
    finally:
        passphrase = ""


def handle_remote_command(request_id, message):
    cancel = threading.Event()
    process = None
    with active_lock:
        active[request_id] = (cancel, None)
    try:
        timeout = max(1, min(int(message.get("timeout", 30)), 300))
        ensure_ssh_agent()
        if not ssh_agent_has_keys():
            raise RuntimeError("no SSH key is loaded; unlock a key in the widget first")
        process = subprocess.Popen(
            build_ssh_argv(message),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
        with active_lock:
            active[request_id] = (cancel, process)
        try:
            output, _ = process.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            process.kill()
            output, _ = process.communicate()
            output += "\n[no exit code: remote command timed out after " + str(timeout) + "s or was killed]"
        if cancel.is_set():
            return
        if "[no exit code:" not in output:
            output += "\n[exit code: " + str(process.returncode) + "]"
        emit(request_id, "command_finished", output=output, returncode=process.returncode, remote=True)
    except Exception as error:
        if not cancel.is_set():
            emit(request_id, "error", message=str(error))
            emit(request_id, "command_finished", output="[remote command failed]", returncode=-1, remote=True)
    finally:
        with active_lock:
            active.pop(request_id, None)


def handle_search(request_id, message):
    try:
        host = str(message.get("host", "http://localhost:8888")).rstrip("/")
        query = urllib.parse.urlencode({"q": str(message.get("query", "")), "format": "json"})
        request = urllib.request.Request(host + "/search?" + query, headers={"User-Agent": "AI-widget/1.0"})
        with urllib.request.urlopen(request, timeout=20) as response:
            text = response.read(2_000_000).decode("utf-8", errors="replace")
        emit(request_id, "search_finished", text=text)
    except Exception as error:
        emit(request_id, "search_finished", text="", error=str(error))


def handle_fetch(request_id, message):
    try:
        url = str(message.get("url", ""))
        request = urllib.request.Request(
            url,
            headers={
                "User-Agent": "Mozilla/5.0 (X11; Linux x86_64; rv:128.0) Gecko/20100101 Firefox/128.0",
                "Accept": "text/html,text/plain,application/json;q=0.9,*/*;q=0.5",
            },
        )
        with urllib.request.urlopen(request, timeout=20) as response:
            if urllib.parse.urlparse(response.geturl()).scheme not in ("http", "https"):
                raise ValueError("redirected to an unsupported protocol")
            data = response.read(3_000_001)
        if len(data) > 3_000_000:
            raise ValueError("page exceeds the 3 MB limit")
        emit(request_id, "fetch_finished", text=data.decode("utf-8", errors="replace"))
    except Exception as error:
        emit(request_id, "fetch_finished", text="", error=str(error))


def handle_screenshot(request_id, message):
    output_path = str(message.get("path", ""))
    try:
        if not output_path:
            raise ValueError("screenshot output path is missing")
        args = ["grim", "-t", "jpeg", "-q", "80", "-s", str(message.get("scale", 0.75))]
        if message.get("scope") == "focused":
            monitors = subprocess.run(
                ["hyprctl", "monitors", "-j"], capture_output=True, check=False, timeout=3, text=True
            )
            if monitors.returncode == 0:
                try:
                    focused = next((item.get("name") for item in json.loads(monitors.stdout) if item.get("focused")), None)
                    if focused:
                        args.extend(["-o", focused])
                except (ValueError, TypeError):
                    pass
        result = subprocess.run(args + ["-"], capture_output=True, check=False, timeout=20)
        if result.returncode != 0 or not result.stdout:
            raise RuntimeError("grim could not capture the screen")
        encoded = base64.b64encode(result.stdout)
        os.makedirs(os.path.dirname(output_path), exist_ok=True)
        with open(output_path, "wb") as image_file:
            os.chmod(output_path, 0o600)
            image_file.write(encoded)
        emit(request_id, "screenshot_finished", text="OK " + str(len(encoded)))
    except FileNotFoundError as error:
        emit(request_id, "screenshot_finished", text="FAIL " + str(error.filename or "required program is missing"))
    except Exception as error:
        emit(request_id, "screenshot_finished", text="FAIL " + str(error))


def transcribe_audio(path, model, language):
    if not os.path.isfile(path) or os.path.getsize(path) == 0:
        return "ERR no audio was recorded"
    binary = shutil.which("whisper-cli") or shutil.which("whisper-cpp") or shutil.which("whisper.cpp")
    if not binary:
        os.unlink(path)
        return "ERR whisper.cpp (whisper-cli) is not installed"
    if not os.path.isfile(model):
        os.unlink(path)
        return "ERR whisper model not found: " + model
    try:
        result = subprocess.run(
            [binary, "-m", model, "-f", path, "-l", language, "-nt", "-np", "-t", "4"],
            capture_output=True,
            check=False,
            timeout=180,
            text=True,
        )
        return "OK " + " ".join(result.stdout.split())
    except Exception as error:
        return "ERR " + str(error)
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass


def record_worker(request_id, state):
    process = state["process"]
    code = process.wait()
    with recordings_lock:
        recordings.pop(request_id, None)
    if state["transcribe"]:
        text = transcribe_audio(state["path"], state["model"], state["language"])
        emit(request_id, "transcribe_finished", text=text)
    else:
        emit(request_id, "record_finished", code=code)


def handle_record_start(request_id, message):
    path = str(message.get("path", ""))
    try:
        os.umask(0o077)
        try:
            os.unlink(path)
        except FileNotFoundError:
            pass
        if shutil.which("pw-record"):
            command = ["pw-record", "--rate", "16000", "--channels", "1", "--format", "s16", path]
        elif shutil.which("arecord"):
            command = ["arecord", "-q", "-f", "S16_LE", "-r", "16000", "-c", "1", "-t", "wav", path]
        else:
            emit(request_id, "record_finished", code=127)
            return
        process = subprocess.Popen(command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        state = {
            "process": process,
            "transcribe": False,
            "path": path,
            "model": str(message.get("model", "")),
            "language": str(message.get("language", "en")),
        }
        with recordings_lock:
            recordings[request_id] = state
        emit(request_id, "record_started")
        threading.Thread(target=record_worker, args=(request_id, state), daemon=True).start()
    except Exception as error:
        emit(request_id, "record_finished", code=1, error=str(error))


def handle_record_stop(target_id):
    with recordings_lock:
        state = recordings.get(target_id)
        if state is not None:
            state["transcribe"] = True
            try:
                state["process"].send_signal(signal.SIGINT)
            except OSError:
                pass


def run_speech(request_id, message):
    cancel = threading.Event()
    with active_lock:
        active[request_id] = (cancel, None)
    code = 3
    try:
        text = str(message.get("text", ""))
        output = str(message.get("output", ""))
        voice = str(message.get("voice", ""))
        host = str(message.get("host", "")).rstrip("/")
        player = shutil.which("pw-play") or shutil.which("paplay") or shutil.which("aplay")
        fallback_used = False
        played = False

        def run(command, data=None, timeout=60):
            nonlocal code
            if cancel.is_set():
                return False
            process = subprocess.Popen(command, stdin=subprocess.PIPE if data is not None else subprocess.DEVNULL,
                                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            with active_lock:
                active[request_id] = (cancel, process)
            try:
                process.communicate(data, timeout=timeout)
            except subprocess.TimeoutExpired:
                process.kill()
                process.communicate()
                return False
            return process.returncode == 0

        if host and player:
            try:
                payload = json.dumps({
                    "model": "kokoro", "input": text, "voice": str(message.get("kokoro_voice", "af_bella")),
                    "response_format": "wav", "speed": float(message.get("speed", 1.0)),
                }).encode("utf-8")
                request = urllib.request.Request(host + "/v1/audio/speech", data=payload,
                                                 headers={"Content-Type": "application/json"}, method="POST")
                with urllib.request.urlopen(request, timeout=60) as response:
                    audio = response.read(20_000_000)
                with open(output, "wb") as audio_file:
                    os.chmod(output, 0o600)
                    audio_file.write(audio)
                if audio and run([player, output]):
                    code = 0
                    return
            except Exception:
                pass
            fallback_used = True

        if shutil.which("piper") and os.path.isfile(voice) and player:
            for flag in ("--output_file", "--output-file"):
                try:
                    with open(output, "wb") as audio_file:
                        audio_file.close()
                    process = subprocess.Popen(["piper", "--model", voice, flag, output], stdin=subprocess.PIPE,
                                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                    process.communicate(text.encode("utf-8"), timeout=60)
                    if process.returncode == 0 and os.path.getsize(output) > 0 and run([player, output]):
                        code = 4 if fallback_used else 0
                        played = True
                        break
                except Exception:
                    continue
        if not played and shutil.which("espeak-ng") and run(["espeak-ng", "--stdin"], text.encode("utf-8")):
            code = 4 if fallback_used else 0
            played = True
        if not played:
            code = 3
    finally:
        try:
            os.unlink(str(message.get("output", "")))
        except OSError:
            pass
        with active_lock:
            active.pop(request_id, None)
        emit(request_id, "speech_finished", code=code, cancelled=cancel.is_set())


def handle_cancel(request_id, message):
    target = str(message.get("target", ""))
    with active_lock:
        entry = active.get(target)
    if entry is not None:
        cancel, process = entry if isinstance(entry, tuple) else (entry, None)
        cancel.set()
        if process is not None:
            try:
                process.terminate()
            except OSError:
                pass
    emit(request_id, "cancel_finished", target=target)


def dispatch(message):
    if not isinstance(message, dict):
        return
    request_id = str(message.get("id", ""))
    action = message.get("action")
    if not request_id:
        return
    if action == "chat":
        threading.Thread(target=handle_chat, args=(request_id, message), daemon=True).start()
    elif action == "command":
        threading.Thread(target=handle_command, args=(request_id, message), daemon=True).start()
    elif action == "remote_command":
        threading.Thread(target=handle_remote_command, args=(request_id, message), daemon=True).start()
    elif action == "agent_start":
        threading.Thread(target=handle_agent_start, args=(request_id, message), daemon=True).start()
    elif action == "agent_unlock":
        threading.Thread(target=handle_agent_unlock, args=(request_id, message), daemon=True).start()
    elif action == "search":
        threading.Thread(target=handle_search, args=(request_id, message), daemon=True).start()
    elif action == "fetch":
        threading.Thread(target=handle_fetch, args=(request_id, message), daemon=True).start()
    elif action == "screenshot":
        threading.Thread(target=handle_screenshot, args=(request_id, message), daemon=True).start()
    elif action == "record_start":
        handle_record_start(request_id, message)
    elif action == "record_stop":
        handle_record_stop(str(message.get("target", "")))
        emit(request_id, "record_stop_finished")
    elif action == "transcribe":
        threading.Thread(target=lambda: emit(request_id, "transcribe_finished", text=transcribe_audio(
            str(message.get("path", "")), str(message.get("model", "")), str(message.get("language", "en")))), daemon=True).start()
    elif action == "speak":
        threading.Thread(target=run_speech, args=(request_id, message), daemon=True).start()
    elif action == "cancel":
        handle_cancel(request_id, message)
    elif action == "ping":
        emit(request_id, "ready")
    elif action == "http":
        threading.Thread(target=handle_http, args=(request_id, message), daemon=True).start()
    elif action == "file":
        threading.Thread(target=handle_file, args=(request_id, message), daemon=True).start()
    else:
        emit(request_id, "error", message="unknown backend action")


def main():
    for line in sys.stdin:
        try:
            dispatch(json.loads(line))
        except (json.JSONDecodeError, TypeError):
            continue


if __name__ == "__main__":
    main()