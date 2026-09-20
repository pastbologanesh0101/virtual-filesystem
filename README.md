# Virtual File System

[![tests](https://github.com/pastbologanesh0101/virtual-filesystem/actions/workflows/tests.yml/badge.svg)](https://github.com/pastbologanesh0101/virtual-filesystem/actions/workflows/tests.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

A fully in-memory, hierarchical virtual filesystem, implemented from scratch
in pure Python (standard library only) — inodes, directories, path
resolution, permissions, and a shell-like CLI on top. Nothing here touches
the real OS filesystem: every `mkdir`, `write`, `mv`, `rm`, etc. mutates a
Python object graph living in process memory. The only exception is the
optional `save` / `load` commands, which serialize that graph to/from a
single JSON file as a session snapshot.

This is a data-structures project, not a shell wrapper: there is no
`os.mkdir`, `open()`, or `subprocess` call anywhere in `vfscore/`.

## Inode model

Every filesystem object — file or directory — is one `Inode` (see
`vfscore/inode.py`), identified by an integer id in a flat table
(`VirtualFileSystem.inodes: dict[int, Inode]`). An `Inode` holds:

| Field         | Meaning                                                            |
|---------------|---------------------------------------------------------------------|
| `id`          | integer inode id, unique for the life of the filesystem            |
| `type`        | `InodeType.FILE` or `InodeType.DIRECTORY`                          |
| `name`        | the entry's own name (authoritative copy lives in parent's map)    |
| `owner`, `group` | identity that created the inode                                 |
| `mode`        | 9-bit Unix-style permission bits (see below)                       |
| `parent_id`   | id of the containing directory (root is its own parent)            |
| `created_at`, `modified_at` | Unix timestamps                                      |
| `content`     | `bytes` (files only)                                                |
| `children`    | `dict[name -> inode_id]` (directories only)                        |

A directory is just a name → inode-id map. There is no separate "directory
entry" object — resolving a path walks these maps directly. Deleting an
inode means removing it from `inodes` (freeing the id) *and* removing its
name from its parent's `children` map; recursive directory removal walks
the subtree post-order, freeing every descendant inode before freeing the
directory itself.

## Permission model

Each inode carries a 9-bit **owner/group/other × read/write/execute**
mode, exactly like a real Unix `chmod` bitmask (`0o644`, `0o755`, ...).
The running session has a single active identity, `current_user` /
`current_group` (default `user` / `users`, settable at
`VirtualFileSystem(user=..., group=...)`). A permission check compares
that identity to the inode's `owner`/`group`:

1. identity == `inode.owner` → use the **owner** (bits 6-8) triplet
2. identity's group == `inode.group` → use the **group** (bits 3-5) triplet
3. otherwise → use the **other** (bits 0-2) triplet

Semantics enforced:

- **read** is required to `cat`/read a file's content, or to `ls` a
  directory's children.
- **write** is required to create/remove/rename an entry inside a
  directory, or to overwrite/append a file's content.
- **execute** is required to `cd` into a directory.

Violating any of these raises `PermissionError` with a message naming the
missing bits, e.g.:

```
error: cannot write 'README.md': permission denied (missing write permission for owner on mode 0o400)
```

This is a deliberate simplification of real Unix permissions: one identity
per session, no supplementary groups, no setuid/sticky bits — but the bit
arithmetic is the real thing, and `chmod` uses real octal notation.

## Path resolution rules

- `/` is the separator; a leading `/` makes a path **absolute** (resolved
  from the root inode); anything else is **relative** to the current
  working directory (`cwd`).
- `.` means "stay", `..` means "go to parent" — chains of them resolve
  left to right (`./a/../b` → `b`).
- `..` at the root stays at the root (root is its own parent), matching
  real Unix behavior.
- Repeated or trailing slashes are ignored: `/a//b/` normalizes to `/a/b`.
- Resolving a path that walks **through** a non-directory component fails
  loudly and leaves the tree untouched — e.g. given a *file* at
  `/file.txt`, resolving `/file.txt/subpath` raises `NotADirectoryError`
  naming `/file.txt` as the offending component. It never silently creates
  or corrupts anything.

All of this lives in `VirtualFileSystem._resolve()` /
`_resolve_parent()` in `vfscore/filesystem.py`.

## Operations implemented

`mkdir` (with `-p`), `touch`, `write`/`read` (+ `append`), `ls`
(with metadata), `cd`, `rm`, `rmdir` (with recursive `-r`, correctly
freeing every descendant inode), `mv` (rename/relocate, cycle-checked so a
directory can't be moved into its own descendant), `cp` (with recursive
`-r`, producing byte-for-byte **independent** copies — mutating a copy
never touches the original), `du` (recursive size), `find` (glob-pattern
search over a subtree), and `chmod`.

## Persistence

`VirtualFileSystem.save(path)` serializes the entire inode table, the
`cwd`, and the session identity to a single JSON file (file content is
base64-encoded so arbitrary bytes round-trip exactly, including embedded
NUL bytes). `VirtualFileSystem.load(path)` / `.from_dict()` rebuild an
identical filesystem, restoring the working directory too. From the CLI:
`save session.json` / `load session.json`.

## Running it

### Interactive REPL

```
python vfs.py
```

```
Virtual File System -- type 'help' for commands, 'exit' to quit.
vfs:/$ mkdir /projects
vfs:/$ cd /projects
vfs:/projects$ touch notes.txt
vfs:/projects$ write notes.txt Hello, virtual world!
vfs:/projects$ cat notes.txt
Hello, virtual world!
vfs:/projects$ ls -l
-rw-r--r-- user     users          22 2026-09-20 12:00:00 notes.txt
vfs:/projects$ chmod 400 notes.txt
vfs:/projects$ write notes.txt overwritten
error: cannot write 'notes.txt': permission denied (missing write permission for owner on mode 0o400)
vfs:/projects$ du /projects
22	/projects
vfs:/projects$ save /tmp/session.json
vfs:/projects$ exit
```

### Script mode (non-interactive, for testing/automation)

```
python vfs.py path/to/script.txt
```

Each non-blank, non-`#`-comment line is one command, executed in order and
echoed with its prompt, e.g.:

```
mkdir -p /a/b/c
touch /a/b/c/file.txt
write /a/b/c/file.txt some content
cat /a/b/c/file.txt
find *.txt /a
```

The process exits with status `1` if any command errored, `0` otherwise —
suitable for CI smoke tests of the CLI layer itself.

### Command reference

| Command | Description |
|---|---|
| `pwd` | print current virtual working directory |
| `ls [-l] [path...]` | list directory contents |
| `cd [path]` | change directory (default `/`) |
| `mkdir [-p] <path...>` | create directory(ies); `-p` creates parents |
| `touch <path...>` | create empty file(s) |
| `write <path> [text...]` | overwrite file content |
| `append <path> [text...]` | append to file content |
| `cat <path...>` | print file content |
| `rm <path...>` | remove file(s) |
| `rmdir [-r] <path...>` | remove (recursively with `-r`) directory(ies) |
| `mv <src> <dst>` | move / rename |
| `cp [-r] <src> <dst>` | copy (recursive for directories) |
| `du [path]` | recursive size in bytes |
| `find <pattern> [path]` | glob-pattern search |
| `chmod <octal> <path>` | change permission bits |
| `whoami` | print current user |
| `save <file.json>` / `load <file.json>` | session persistence |
| `help` / `exit` \| `quit` | |

## Tests

```
python -m unittest discover -s tests -v
# or
python -m pytest tests/ -v
```

29 unit tests in `tests/test_filesystem.py` cover inode creation, read/write
round-trips, `ls` metadata, path resolution (absolute/relative/`.`/`..`
chains, repeated slashes, root `..`), recursive removal and inode freeing,
`mv`/`cp` correctness (including verifying copies are independent of their
source), permission enforcement, `du`, `find`, non-directory-component path
errors, and full JSON save/load round-trips. `.github/workflows/tests.yml`
runs the suite on Python 3.11 and 3.12 on every push/PR.

## Project layout

```
vfs.py                    # CLI entry point (REPL + script mode)
vfscore/
  inode.py                # Inode / InodeType data model
  filesystem.py           # VirtualFileSystem: resolution, ops, persistence
tests/
  test_filesystem.py      # unit tests
.github/workflows/tests.yml
```
