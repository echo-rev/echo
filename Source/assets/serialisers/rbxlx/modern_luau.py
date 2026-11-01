from xml.etree.ElementTree import Element

from .. import luau


def replace(root: Element) -> Element | None:
    '''
    Rewrites every script's `Source` so code from modern Studio runs on the 2021 engine.
    '''
    for item in root.iter('Item'):
        if not item.get('class', '').endswith('Script'):
            continue
        for prop in item.iterfind('Properties/ProtectedString'):
            if prop.get('name') == 'Source' and prop.text:
                prop.text = luau.downgrade(prop.text)
    return root
