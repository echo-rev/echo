from . import _logic


def replace(parser: _logic.rbxl_parser, chunk_data: _logic.chunk_data_type) -> _logic.chunk_data_type | None:
    '''
    Newer files store CollectionService `Tags` as a SharedString (0x1C); 2021 engines expect
    a plain string (0x01). SharedString values are interleaved big-endian uint32 indices
    into the SSTR chunk: https://dom.rojo.space/binary.html#sharedstring
    '''
    if not isinstance(chunk_data, _logic.chunk_data_type_prop):
        return None
    if chunk_data.prop_name != b'Tags' or chunk_data.prop_type != 0x1C:
        return None

    count = parser.class_dict[chunk_data.class_iden].instance_count
    raw = chunk_data.prop_values
    strings = parser.sstr_list or []
    values = []
    for i in range(count):
        index = int.from_bytes(bytes(raw[i + k * count] for k in range(4)), byteorder='big')
        values.append(strings[index] if index < len(strings) else b'')

    chunk_data.prop_type = 0x01
    chunk_data.prop_values = _logic.join_prop_strings(values)
    return chunk_data
