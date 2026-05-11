import asyncio
import os
import pty
import struct
import fcntl
import termios
import uuid
from typing import Dict
from fastapi import WebSocket, WebSocketDisconnect

class TerminalManager:
    def __init__(self):
        self.processes: Dict[str, dict] = {}

    async def create_host_terminal(self, websocket: WebSocket, cols: int = 80, rows: int = 24):
        session_id = str(uuid.uuid4())

        master_fd, slave_fd = pty.openpty()

        env = os.environ.copy()
        env['TERM'] = 'xterm-256color'
        env['HOME'] = '/host/root'
        env['PATH'] = '/host/bin:/host/usr/bin:/host/usr/local/bin:/bin:/usr/bin'

        pid = os.fork()
        
        if pid == 0:
            os.close(master_fd)
            os.setsid()
            
            os.dup2(slave_fd, 0)
            os.dup2(slave_fd, 1)
            os.dup2(slave_fd, 2)
            
            os.close(slave_fd)
            
            os.chdir('/host')
            
            if os.path.exists('/host/bin/bash'):
                shell_path = '/host/bin/bash'
            elif os.path.exists('/host/bin/sh'):
                shell_path = '/host/bin/sh'
            elif os.path.exists('/host/usr/bin/bash'):
                shell_path = '/host/usr/bin/bash'
            elif os.path.exists('/host/usr/bin/sh'):
                shell_path = '/host/usr/bin/sh'
            else:
                shell_path = '/bin/bash'
            
            os.execvp(shell_path, [shell_path])
        else:
            os.close(slave_fd)

            self.processes[session_id] = {
                'pid': pid,
                'master_fd': master_fd,
                'ws': websocket
            }

            try:
                fcntl.ioctl(master_fd, termios.TIOCSWINSZ, struct.pack('HHHH', rows, cols, 0, 0))
            except Exception:
                pass

            await websocket.send_json({"type": "connected", "session_id": session_id})

            async def read_loop():
                try:
                    while True:
                        await asyncio.sleep(0.01)
                        try:
                            import select
                            if select.select([master_fd], [], [], 0)[0]:
                                data = os.read(master_fd, 4096)
                                if data:
                                    await websocket.send_json({
                                        "type": "output",
                                        "data": data.decode('utf-8', errors='replace')
                                    })
                                else:
                                    break
                        except (OSError, IOError):
                            break
                except Exception:
                    pass

            async def write_loop():
                try:
                    while True:
                        msg = await websocket.receive_json()
                        if msg.get('type') == 'input':
                            data = msg.get('data', '')
                            if data:
                                os.write(master_fd, data.encode('utf-8'))
                        elif msg.get('type') == 'resize':
                            cols = msg.get('cols', 80)
                            rows = msg.get('rows', 24)
                            try:
                                fcntl.ioctl(master_fd, termios.TIOCSWINSZ, struct.pack('HHHH', rows, cols, 0, 0))
                            except Exception:
                                pass
                except WebSocketDisconnect:
                    pass

            read_task = asyncio.create_task(read_loop())
            write_task = asyncio.create_task(write_loop())

            try:
                await asyncio.gather(read_task, write_task)
            finally:
                try:
                    os.close(master_fd)
                except Exception:
                    pass
                try:
                    os.kill(pid, 9)
                except Exception:
                    pass
                if session_id in self.processes:
                    del self.processes[session_id]

terminal_manager = TerminalManager()