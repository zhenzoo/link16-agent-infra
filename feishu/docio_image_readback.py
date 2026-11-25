"""Verify image bytes when Feishu copies native image tokens to the destination."""
import re
import hashlib
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from tempfile import TemporaryDirectory
from xml.etree import ElementTree as ET

from doc_structure import DocStructureError


def image_source_hashes(mapping, submitted_xml):
    if not isinstance(mapping, dict) or not mapping:
        raise DocStructureError('图片回读须指定原token与SHA256')
    submitted = ET.fromstring('<doc>' + submitted_xml + '</doc>')
    tokens = {n.get('src') for n in submitted.iter('img')}
    for token, sha in mapping.items():
        if token not in tokens or not isinstance(sha, str) or not re.fullmatch('[a-f0-9]{64}', sha):
            raise DocStructureError('图片摘要不属于本次写入的原生图片或不是有效SHA256')
    return mapping


def copied_image_aliases(expected, actual, known_sha, load_sha):
    reference = ET.fromstring('<doc>' + expected + '</doc>')
    result = ET.fromstring('<doc>' + actual + '</doc>')
    def positions(root):
        indexed = []
        def visit(node, path):
            if node.tag == 'img':
                indexed.append((path,node))
            # Server metadata for table widths can appear after writes.
            children = [child for child in node if child.tag not in ('colgroup', 'col')]
            for i, child in enumerate(children):
                visit(child, path + ((child.tag,i),))
        visit(root, ())
        return indexed
    source, copied = positions(reference), positions(result)
    if [p for p,_ in source] != [p for p,_ in copied]:
        raise DocStructureError('图片所属单元格、位置或数量与本次写入不符')
    verified = {}
    cache = {}
    def checksum(token):
        if token not in cache:
            cache[token] = load_sha(token)
        return cache[token]
    for (_, original), (_, published) in zip(source, copied):
        old, new = original.get('src'), published.get('src')
        if old not in known_sha:
            if old != new:
                raise DocStructureError('未授权替换的原图token发生变化')
            continue
        if not new or checksum(old) != known_sha[old] or checksum(new) != known_sha[old]:
            raise DocStructureError('飞书原件、落地图片与已登记的SHA256不一致')
        verified[new] = {'source_token':old,'sha256':known_sha[old]}
        published.set('src', old)
    return ''.join(ET.tostring(n,encoding='unicode') for n in result), verified


def verify_published_images(api, bot, expected, actual, allowed):
    with TemporaryDirectory() as temp:
        folder = Path(temp)
        def load_sha(token):
            target = folder / (hashlib.sha256(token.encode()).hexdigest() + '.bin')
            record, error = api._fetch_media(token, target, bot, folder, 120)
            if not record or not record.get('sha256'):
                raise DocStructureError('不能回读已复制图片的原始字节：' + str(error)[:180])
            return record['sha256']
        originals = ET.fromstring('<doc>' + expected + '</doc>').iter('img')
        published = ET.fromstring('<doc>' + actual + '</doc>').iter('img')
        tokens = {token for original, copy in zip(originals, published) if original.get('src') in allowed
                  for token in (original.get('src'), copy.get('src')) if token}
        ordered = sorted(tokens)
        with ThreadPoolExecutor(max_workers=3) as workers:
            checksums = dict(zip(ordered, workers.map(load_sha, ordered)))
        return copied_image_aliases(expected, actual, allowed, checksums.__getitem__)
