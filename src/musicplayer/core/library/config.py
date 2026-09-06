import struct

MAGIC = b"MMD1"
VERSION = 3
HEADER = struct.Struct("<4sBI")
U32 = struct.Struct("<I")
TRACK_FIXED = struct.Struct("<16sd")
UUID_ONLY = struct.Struct("<16s")
SHA256_BYTES = 32
