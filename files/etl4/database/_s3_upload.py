"""Shared S3 object upload for step 39: verify content, use conditional writes, and clean up failed multipart uploads.
39 共用的 S3 对象上传：内容核对、条件写入和失败分片清理。
"""

import base64
import hashlib
import mimetypes
from pathlib import Path
import zlib


def crc32_text(value):
    return base64.b64encode((value & 0xffffffff).to_bytes(4, 'big')).decode('ascii')


def file_checks(path, expected_sha256, expected_size):
    """Verify source SHA256 and calculate the S3 full-object CRC32 in one file read.
    一次读取同时核对来源 SHA256，并计算 S3 所用的完整 CRC32。
    """
    path = Path(path)
    if not path.is_file() or path.stat().st_size != expected_size:
        raise ValueError('The source file is missing or its size differs from the manifest: ' + path.name)
    digest = hashlib.sha256()
    checksum = 0
    size = 0
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(block)
            checksum = zlib.crc32(block, checksum)
            size += len(block)
    if size != expected_size or digest.hexdigest() != expected_sha256:
        raise ValueError('The source file content differs from the manifest: ' + path.name)
    return crc32_text(checksum)


def error_code(error):
    return str(getattr(error, 'response', {}).get('Error', {}).get('Code', ''))


def check_upload_sdk(client):
    """Reject older SDKs before upload when conditional-write or full-object checksum parameters are missing.
    旧 SDK 缺少条件写或完整校验参数时，先报错再上传。
    """
    required = {
        'PutObject': {'ChecksumAlgorithm', 'ChecksumCRC32', 'IfNoneMatch'},
        'HeadObject': {'ChecksumMode'},
        'CreateMultipartUpload': {'ChecksumAlgorithm', 'ChecksumType'},
        'UploadPart': {'ChecksumAlgorithm', 'ChecksumCRC32'},
        'CompleteMultipartUpload': {'ChecksumType', 'ChecksumCRC32', 'MpuObjectSize', 'IfNoneMatch'},
    }
    for operation, fields in required.items():
        available = client.meta.service_model.operation_model(operation).input_shape.members
        if not fields.issubset(available):
            raise ValueError('The installed boto3/botocore lacks full-object checksum APIs; upgrade the AWS upload dependencies')


def remote_object(client, bucket, key, expected_sha256, expected_size, crc32):
    # HEAD API reference / HEAD 接口文档: https://docs.aws.amazon.com/boto3/latest/reference/services/s3/client/head_object.html
    try:
        head = client.head_object(Bucket=bucket, Key=key, ChecksumMode='ENABLED')
    except Exception as exc:
        if error_code(exc) in ('404', 'NoSuchKey', 'NotFound'):
            return None
        raise
    if head.get('ContentLength') != expected_size:
        raise ValueError('The existing object has a size conflict; it was not overwritten: ' + key)
    recorded_sha = head.get('Metadata', {}).get('source-sha256')
    if recorded_sha and recorded_sha != expected_sha256:
        raise ValueError('The existing object has a content identity conflict; it was not overwritten: ' + key)
    sha_base64 = base64.b64encode(bytes.fromhex(expected_sha256)).decode('ascii')
    if head.get('ChecksumType') != 'FULL_OBJECT':
        raise ValueError('The existing object has no comparable full-object checksum; use a new prefix: ' + key)
    if head.get('ChecksumSHA256'):
        if head['ChecksumSHA256'] != sha_base64:
            raise ValueError('The existing object has a SHA256 conflict; it was not overwritten: ' + key)
        algorithm, checksum = 'SHA256', sha_base64
    elif head.get('ChecksumCRC32') == crc32:
        algorithm, checksum = 'CRC32', crc32
    else:
        raise ValueError('The existing object checksum is missing or does not match; it was not overwritten: ' + key)
    return {'sha256': expected_sha256, 'byte_size': expected_size, 'object_key': key,
            'checksum_algorithm': algorithm, 'checksum_type': 'FULL_OBJECT', 'checksum': checksum,
            'etag': head.get('ETag'), 'version_id': head.get('VersionId')}


class UploadStream:
    """Forward SDK file-read progress to the entry point; determine completion from the server response.
    把 SDK 读取文件的进度交给入口显示；完成状态仍以服务端回复为准。
    """

    def __init__(self, stream, size, progress):
        self.stream = stream
        self.size = size
        self.progress = progress

    def read(self, count=-1):
        data = self.stream.read(count)
        if self.progress:
            self.progress(self.stream.tell(), self.size)
        return data

    def __getattr__(self, name):
        return getattr(self.stream, name)


def upload_multipart(client, source, bucket, key, size, sha256, crc32, part_size, progress):
    # Multipart SHA256 is not a whole-file digest, so explicitly use CRC32 FULL_OBJECT here. / SHA256 的 multipart 值不是整文件摘要，因此这里明确使用 CRC32 FULL_OBJECT。
    # Upload integrity reference / 上传完整性文档: https://docs.aws.amazon.com/AmazonS3/latest/userguide/checking-object-integrity-upload.html
    created = client.create_multipart_upload(
        Bucket=bucket, Key=key, ChecksumAlgorithm='CRC32', ChecksumType='FULL_OBJECT',
        Metadata={'source-sha256': sha256},
        ContentType=mimetypes.guess_type(source.name)[0] or 'application/octet-stream')
    upload_id = created['UploadId']
    try:
        parts = []
        sent = 0
        with source.open('rb') as stream:
            for number in range(1, 10001):
                block = stream.read(part_size)
                if not block:
                    break
                part_crc = crc32_text(zlib.crc32(block))
                result = client.upload_part(
                    Bucket=bucket, Key=key, UploadId=upload_id, PartNumber=number,
                    Body=block, ContentLength=len(block),
                    ChecksumAlgorithm='CRC32', ChecksumCRC32=part_crc)
                if result.get('ChecksumCRC32') != part_crc:
                    raise ValueError('The S3 part checksum response does not match: ' + key)
                parts.append({'PartNumber': number, 'ETag': result['ETag'], 'ChecksumCRC32': part_crc})
                sent += len(block)
                if progress:
                    progress(sent, size)
            if stream.read(1) or sent != size:
                raise ValueError('The source size changed during upload or the part count exceeded the limit: ' + key)
        client.complete_multipart_upload(
            Bucket=bucket, Key=key, UploadId=upload_id, MultipartUpload={'Parts': parts},
            ChecksumCRC32=crc32, ChecksumType='FULL_OBJECT', MpuObjectSize=size, IfNoneMatch='*')
    except BaseException:
        # Restart incomplete large uploads; reuse completed objects through HEAD on a later run. / 未完成大文件重新上传；已完成对象可在下次运行中通过 HEAD 复用。
        try:
            client.abort_multipart_upload(Bucket=bucket, Key=key, UploadId=upload_id)
        except Exception:
            pass
        raise


def upload_object(client, source, bucket, key, expected_sha256, expected_size, *,
                  progress=None, part_size=64 * 1024**2, multipart_threshold=64 * 1024**2):
    """Upload or reuse one object without overwriting existing content or downloading the full file with GET.
    上传或复用一个对象；不覆盖已有内容，也不 GET 下载整文件。
    """
    source = Path(source)
    if part_size < 5 * 1024**2 or part_size > 5 * 1024**3:
        raise ValueError('The S3 part size must be between 5 MiB and 5 GiB')
    if (type(expected_size) is not int or expected_size < 0
            or not isinstance(expected_sha256, str) or len(expected_sha256) != 64
            or any(value not in '0123456789abcdef' for value in expected_sha256)):
        raise ValueError('Invalid file identity format')
    crc32 = file_checks(source, expected_sha256, expected_size)
    existing = remote_object(client, bucket, key, expected_sha256, expected_size, crc32)
    if existing:
        return {**existing, 'reused': True}
    if progress:
        progress(0, expected_size)
    try:
        if expected_size >= multipart_threshold or expected_size > 5 * 1024**3:
            # Allow at most 10000 parts, rounding sizes up to MiB so memory usage depends only on a single part. / 最多 10000 片，按 MiB 向上调整，保持内存占用只与单片有关。
            minimum = ((expected_size + 9999) // 10000 + 1024**2 - 1) // 1024**2 * 1024**2
            selected_size = max(part_size, minimum)
            if selected_size > 5 * 1024**3:
                raise ValueError('The file exceeds the size supported by S3 multipart upload')
            upload_multipart(client, source, bucket, key, expected_size, expected_sha256,
                             crc32, selected_size, progress)
        else:
            with source.open('rb') as stream:
                client.put_object(
                    Bucket=bucket, Key=key, Body=UploadStream(stream, expected_size, progress),
                    ContentLength=expected_size, ChecksumAlgorithm='CRC32', ChecksumCRC32=crc32,
                    ContentType=mimetypes.guess_type(source.name)[0] or 'application/octet-stream',
                    Metadata={'source-sha256': expected_sha256}, IfNoneMatch='*')
    except Exception as exc:
        if error_code(exc) not in ('412', 'PreconditionFailed'):
            raise
        # If another process completes the same object after HEAD, verify its content before reuse. / HEAD 之后有另一进程完成同一对象时，仍须核对内容才能复用。
        existing = remote_object(client, bucket, key, expected_sha256, expected_size, crc32)
        if not existing:
            raise ValueError('No complete object was found after a conditional-write conflict: ' + key) from exc
        return {**existing, 'reused': True}
    uploaded = remote_object(client, bucket, key, expected_sha256, expected_size, crc32)
    if not uploaded:
        raise ValueError('No complete object was found after upload: ' + key)
    if progress:
        progress(expected_size, expected_size)
    return {**uploaded, 'reused': False}
