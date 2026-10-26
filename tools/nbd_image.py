#!/usr/bin/env python3
"""Serve a regular file as a read-only oldstyle NBD export over USB TCP."""

import argparse
import errno
import os
import socket
import stat
import struct


NBDMAGIC = b"NBDMAGIC"
OLDSTYLE_MAGIC = 0x00420281861253
REQUEST_MAGIC = 0x25609513
REPLY_MAGIC = 0x67446698
READ = 0
WRITE = 1
DISCONNECT = 2
FLUSH = 3


def receive_exact(conn, size):
    data = bytearray()
    while len(data) < size:
        chunk = conn.recv(size - len(data))
        if not chunk:
            raise EOFError
        data.extend(chunk)
    return bytes(data)


def serve_client(conn, image_fd, image_size):
    conn.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
    export_flags = 3
    conn.sendall(
        NBDMAGIC + struct.pack(">QQI", OLDSTYLE_MAGIC, image_size, export_flags) + bytes(124)
    )
    reads = 0
    while True:
        try:
            magic, flags, command, cookie, offset, length = struct.unpack(
                ">IHHQQI", receive_exact(conn, 28)
            )
        except EOFError:
            break
        if magic != REQUEST_MAGIC:
            raise ValueError(f"invalid request magic: {magic:#x}")
        if command == DISCONNECT:
            break
        if length > 32 * 1024 * 1024:
            raise ValueError("request exceeds 32 MiB")
        if command == WRITE:
            receive_exact(conn, length)
        error = 0
        payload = b""
        if command == READ:
            if offset > image_size or length > image_size - offset:
                error = errno.EINVAL
            else:
                payload = os.pread(image_fd, length, offset)
                if len(payload) != length:
                    raise IOError("short image read")
                reads += 1
        elif command == FLUSH:
            pass
        elif command == WRITE:
            error = errno.EROFS
        else:
            error = errno.EOPNOTSUPP
        conn.sendall(struct.pack(">IIQ", REPLY_MAGIC, error, cookie) + payload)
    print(f"client disconnected after {reads} reads", flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("image")
    parser.add_argument("--bind", required=True)
    parser.add_argument("--port", type=int, default=10810)
    args = parser.parse_args()
    image_fd = os.open(args.image, os.O_RDONLY)
    image_info = os.fstat(image_fd)
    if not stat.S_ISREG(image_info.st_mode):
        parser.error("backing image must be a regular file")
    image_size = image_info.st_size
    if not image_size or image_size % 4096:
        parser.error("image must have a nonzero, 4096-byte-aligned size")
    try:
        with socket.socket() as server:
            server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            server.bind((args.bind, args.port))
            server.listen(1)
            print(f"read-only NBD {image_size} bytes on {args.bind}:{args.port}", flush=True)
            while True:
                conn, peer = server.accept()
                print(f"client {peer[0]} connected", flush=True)
                with conn:
                    try:
                        serve_client(conn, image_fd, image_size)
                    except (ConnectionError, OSError, ValueError) as error:
                        print(f"client ended: {error}", flush=True)
    except KeyboardInterrupt:
        print("server stopped", flush=True)
    finally:
        os.close(image_fd)


if __name__ == "__main__":
    main()
