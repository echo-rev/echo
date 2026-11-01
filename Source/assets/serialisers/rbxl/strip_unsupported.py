from . import _logic

REMOVAL_SET = {
    (b'Capabilities', 0x21),
    (b'UniqueId', 0x1F),
}


def compute_hash(s: bytes, p=7, m=0x100):
    hash_value = 0
    power = 1
    for ch in s:
        hash_value = (hash_value + (ch - ord('a') + 1) * power) % m
        power = (power * p) % m
    return hash_value


# 0x1F UniqueId, 0x20 Font, 0x21 SecurityCapabilities, 0x22 Content and anything newer:
# 2021 engines can't read these types at all (Studio 2021 crashes on them), so whatever the
# other converters didn't turn into an older type is dropped. This method runs last.
FIRST_UNSUPPORTED_TYPE = 0x1F


def should_strip(chunk_data: _logic.chunk_data_type_prop) -> bool:
    if chunk_data.prop_type >= FIRST_UNSUPPORTED_TYPE:
        return True
    if (chunk_data.prop_name, chunk_data.prop_type) in REMOVAL_SET:
        return True
    if (chunk_data.prop_name) in REMOVAL_SET:
        return True
    return False


def replace(parser: _logic.rbxl_parser, chunk_data: _logic.chunk_data_type) -> _logic.chunk_data_type | None:
    '''
    This function removes arbitrary PROP types from an RBXL.
    If you want to find the culprit a bad RBXL which crashes clients, modify `REMOVAL_SET`.
    '''
    if not isinstance(chunk_data, _logic.chunk_data_type_prop):
        return None

    if not should_strip(chunk_data):
        return None

    chunk_data.prop_name = b''

    # 0x02: boolean type; it takes up just one byte in `prop_values` per class instance.
    chunk_data.prop_type = 0x02

    instance_count = parser.class_dict[chunk_data.class_iden].instance_count
    chunk_data.prop_values = b'\x00' * instance_count

    return chunk_data
