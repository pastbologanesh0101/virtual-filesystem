"""vfscore: a pure-stdlib, in-memory virtual filesystem.

Public API:
    VirtualFileSystem  -- the filesystem itself (path resolution, ops, persistence)
    Inode, InodeType    -- the on-disk (in-memory) data model
"""

from .inode import Inode, InodeType
from .filesystem import VirtualFileSystem

__all__ = ["Inode", "InodeType", "VirtualFileSystem"]
