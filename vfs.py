#!/usr/bin/env python3
"""Virtual File System -- a shell-like CLI over a pure in-memory filesystem.

Usage:
    python vfs.py                  # interactive REPL
    python vfs.py script.txt       # run commands from a script file, non-interactively
    python vfs.py --load save.json # start the REPL restoring a saved session

Every operation below acts ONLY on the in-memory data structure defined in
vfscore/ -- nothing here ever touches the real filesystem (aside from the
optional `save`/`load` commands, which read/write a single JSON snapshot
file on the real disk, purely as a serialization format).
"""

from __future__ import annotations

import shlex
import sys
from typing import List, Optional

from vfscore import VirtualFileSystem


def fmt_size(n: int) -> str:
    return f"{n}"


def fmt_time(ts: float) -> str:
    import datetime

    return datetime.datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S")


class Shell:
    """Parses and dispatches command lines against a VirtualFileSystem."""

    def __init__(self, fs: Optional[VirtualFileSystem] = None) -> None:
        self.fs = fs or VirtualFileSystem(user="user", group="users")
        self.running = True

    # -- dispatch --------------------------------------------------------

    def run_line(self, line: str) -> Optional[str]:
        """Execute one command line. Returns output text (or None), raises
        nothing -- errors are caught and returned as '<error message>' text
        prefixed appropriately by the caller."""
        line = line.strip()
        if not line or line.startswith("#"):
            return None
        try:
            args = shlex.split(line)
        except ValueError as exc:
            raise CommandError(f"parse error: {exc}")
        if not args:
            return None
        cmd, rest = args[0], args[1:]
        handler = self.COMMANDS.get(cmd)
        if handler is None:
            raise CommandError(f"{cmd}: command not found")
        return handler(self, rest)

    # -- individual commands ------------------------------------------------

    def cmd_pwd(self, args: List[str]) -> str:
        return self.fs.getcwd()

    def cmd_ls(self, args: List[str]) -> str:
        long_form = "-l" in args
        paths = [a for a in args if a != "-l"] or ["."]
        blocks = []
        for path in paths:
            entries = self.fs.listdir(path)
            if not long_form:
                blocks.append("  ".join(e.name for e in entries))
            else:
                lines = []
                for e in entries:
                    lines.append(
                        f"{e.mode_string()} {e.owner:8s} {e.group:8s} "
                        f"{fmt_size(e.size):>8s} {fmt_time(e.modified_at)} {e.name}"
                    )
                blocks.append("\n".join(lines))
        return "\n".join(blocks)

    def cmd_cd(self, args: List[str]) -> None:
        target = args[0] if args else "/"
        self.fs.chdir(target)
        return None

    def cmd_mkdir(self, args: List[str]) -> None:
        parents = "-p" in args
        paths = [a for a in args if a != "-p"]
        if not paths:
            raise CommandError("mkdir: missing path operand")
        for p in paths:
            self.fs.mkdir(p, parents=parents)
        return None

    def cmd_touch(self, args: List[str]) -> None:
        if not args:
            raise CommandError("touch: missing path operand")
        for p in args:
            self.fs.touch(p)
        return None

    def cmd_write(self, args: List[str]) -> None:
        if len(args) < 1:
            raise CommandError("write: usage: write <path> [text...]")
        path, text = args[0], " ".join(args[1:])
        self.fs.write_file(path, text.encode("utf-8"))
        return None

    def cmd_append(self, args: List[str]) -> None:
        if len(args) < 1:
            raise CommandError("append: usage: append <path> [text...]")
        path, text = args[0], " ".join(args[1:])
        self.fs.write_file(path, text.encode("utf-8"), append=True)
        return None

    def cmd_cat(self, args: List[str]) -> str:
        if not args:
            raise CommandError("cat: missing path operand")
        chunks = [self.fs.read_file(p).decode("utf-8", errors="replace") for p in args]
        return "".join(chunks)

    def cmd_rm(self, args: List[str]) -> None:
        if not args:
            raise CommandError("rm: missing path operand")
        for p in args:
            self.fs.remove_file(p)
        return None

    def cmd_rmdir(self, args: List[str]) -> None:
        recursive = "-r" in args or "-rf" in args
        paths = [a for a in args if a not in ("-r", "-rf")]
        if not paths:
            raise CommandError("rmdir: missing path operand")
        for p in paths:
            self.fs.rmdir(p, recursive=recursive)
        return None

    def cmd_mv(self, args: List[str]) -> None:
        if len(args) != 2:
            raise CommandError("mv: usage: mv <src> <dst>")
        self.fs.move(args[0], args[1])
        return None

    def cmd_cp(self, args: List[str]) -> None:
        recursive = "-r" in args
        paths = [a for a in args if a != "-r"]
        if len(paths) != 2:
            raise CommandError("cp: usage: cp [-r] <src> <dst>")
        self.fs.copy(paths[0], paths[1], recursive=recursive)
        return None

    def cmd_du(self, args: List[str]) -> str:
        path = args[0] if args else "."
        return f"{self.fs.du(path)}\t{path}"

    def cmd_find(self, args: List[str]) -> str:
        if not args:
            raise CommandError("find: usage: find <pattern> [start_path]")
        pattern = args[0]
        start = args[1] if len(args) > 1 else "."
        return "\n".join(self.fs.find(pattern, start))

    def cmd_chmod(self, args: List[str]) -> None:
        if len(args) != 2:
            raise CommandError("chmod: usage: chmod <octal-mode> <path>")
        mode = int(args[0], 8)
        self.fs.chmod(args[1], mode)
        return None

    def cmd_whoami(self, args: List[str]) -> str:
        return self.fs.current_user

    def cmd_save(self, args: List[str]) -> None:
        if not args:
            raise CommandError("save: usage: save <file.json>")
        self.fs.save(args[0])
        return None

    def cmd_load(self, args: List[str]) -> None:
        if not args:
            raise CommandError("load: usage: load <file.json>")
        self.fs = VirtualFileSystem.load(args[0])
        return None

    def cmd_help(self, args: List[str]) -> str:
        return HELP_TEXT

    def cmd_exit(self, args: List[str]) -> None:
        self.running = False
        return None

    COMMANDS = {
        "pwd": cmd_pwd,
        "ls": cmd_ls,
        "cd": cmd_cd,
        "mkdir": cmd_mkdir,
        "touch": cmd_touch,
        "write": cmd_write,
        "append": cmd_append,
        "cat": cmd_cat,
        "rm": cmd_rm,
        "rmdir": cmd_rmdir,
        "mv": cmd_mv,
        "cp": cmd_cp,
        "du": cmd_du,
        "find": cmd_find,
        "chmod": cmd_chmod,
        "whoami": cmd_whoami,
        "save": cmd_save,
        "load": cmd_load,
        "help": cmd_help,
        "exit": cmd_exit,
        "quit": cmd_exit,
    }


class CommandError(Exception):
    pass


HELP_TEXT = """\
Commands:
  pwd                        print current virtual working directory
  ls [-l] [path...]          list directory contents
  cd [path]                  change directory (default: /)
  mkdir [-p] <path...>       create directory (-p: create parents too)
  touch <path...>            create empty file(s)
  write <path> [text...]     overwrite file content with text
  append <path> [text...]    append text to file content
  cat <path...>               print file content
  rm <path...>                remove file(s)
  rmdir [-r] <path...>        remove empty directory (-r: recursive)
  mv <src> <dst>              move/rename
  cp [-r] <src> <dst>         copy (recursive for directories)
  du [path]                   recursive size in bytes (default: cwd)
  find <pattern> [path]       find entries by glob pattern (default: cwd)
  chmod <octal> <path>        change permission bits, e.g. chmod 600 f.txt
  whoami                      print current user
  save <file.json>            save session to a JSON file on the real disk
  load <file.json>            load session from a JSON file on the real disk
  help                        show this text
  exit | quit                 leave the shell
"""


def run_repl(fs: Optional[VirtualFileSystem] = None) -> None:
    shell = Shell(fs)
    print("Virtual File System -- type 'help' for commands, 'exit' to quit.")
    while shell.running:
        try:
            line = input(f"vfs:{shell.fs.getcwd()}$ ")
        except (EOFError, KeyboardInterrupt):
            print()
            break
        try:
            output = shell.run_line(line)
            if output:
                print(output)
        except (CommandError, OSError) as exc:
            print(f"error: {exc}", file=sys.stderr)


def run_script(filepath: str, fs: Optional[VirtualFileSystem] = None, echo: bool = True) -> int:
    """Run commands from a file, one per line. Returns the number of errors."""
    shell = Shell(fs)
    errors = 0
    with open(filepath, "r", encoding="utf-8") as f:
        lines = f.readlines()
    for raw in lines:
        line = raw.rstrip("\n")
        if not line.strip() or line.strip().startswith("#"):
            continue
        if echo:
            print(f"vfs:{shell.fs.getcwd()}$ {line}")
        try:
            output = shell.run_line(line)
            if output:
                print(output)
        except (CommandError, OSError) as exc:
            print(f"error: {exc}", file=sys.stderr)
            errors += 1
        if not shell.running:
            break
    return errors


def main(argv: List[str]) -> int:
    load_path = None
    script_path = None
    args = argv[:]
    while args:
        a = args.pop(0)
        if a == "--load":
            load_path = args.pop(0)
        elif a == "--help" or a == "-h":
            print(__doc__)
            return 0
        else:
            script_path = a

    fs = VirtualFileSystem.load(load_path) if load_path else None

    if script_path:
        errors = run_script(script_path, fs)
        return 1 if errors else 0

    run_repl(fs)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
