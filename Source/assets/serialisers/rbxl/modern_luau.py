from . import _logic
from .. import luau


def replace(parser: _logic.rbxl_parser, chunk_data: _logic.chunk_data_type) -> _logic.chunk_data_type | None:
    '''
    Rewrites every script's `Source` so code from modern Studio runs on the 2021 engine.
    '''
    if not isinstance(chunk_data, _logic.chunk_data_type_prop):
        return None
    if chunk_data.prop_name != b'Source' or chunk_data.prop_type != 0x01:
        return None
    if not parser.class_dict[chunk_data.class_iden].class_name.endswith(b'Script'):
        return None

    chunk_data.prop_values = _logic.join_prop_strings([
        # surrogateescape round-trips any bytes that aren't valid UTF-8
        luau.downgrade(v.decode('utf-8', 'surrogateescape')).encode('utf-8', 'surrogateescape')
        for v in _logic.split_prop_strings(chunk_data.prop_values)
    ])
    return chunk_data
