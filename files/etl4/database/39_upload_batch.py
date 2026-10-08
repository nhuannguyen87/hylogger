"""Upload the batch manifest exported by step 38 and record per-object progress without recomputing business data in the cloud.
上传步骤 38 的本批导出清单，保存逐对象进度，不进行云端业务重算。
"""

import base64
import http.client
from pathlib import Path
import ssl
import sys
import threading
import time
from datetime import datetime, timezone
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import unquote, urlsplit
from xml.etree import ElementTree

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from _settings import StepParser, configure
import _s3_upload

SINGLE_PUT_LIMIT = 5 * 1024**3
SAFE_S3_ERROR_CODES = {
    "AccessDenied", "ExpiredToken", "InvalidToken", "TokenRefreshRequired", "RequestExpired",
    "SignatureDoesNotMatch", "AuthorizationQueryParametersError", "InvalidAccessKeyId", "RequestTimeTooSkewed",
    "BadDigest", "InvalidDigest", "InvalidRequest", "InvalidArgument", "EntityTooLarge", "EntityTooSmall",
    "IncompleteBody", "MissingContentLength", "PreconditionFailed", "ConditionalRequestConflict",
    "SlowDown", "InternalError", "ServiceUnavailable", "RequestTimeout", "NoSuchBucket", "NoSuchKey",
    "PermanentRedirect", "TemporaryRedirect", "MethodNotAllowed", "NotImplemented",
}


def safe_s3_error_code(payload):
    """Keep only known AWS error codes; omit messages, request headers, and signed URLs from diagnostics.
    只保留已知 AWS Code；Message、请求头和签名 URL 都不进入诊断。
    """
    if len(payload) > 65536 or b"<!DOCTYPE" in payload.upper() or b"<!ENTITY" in payload.upper():
        return None
    try:
        root = ElementTree.fromstring(payload)
    except ElementTree.ParseError:
        return None
    if root.tag.rsplit("}", 1)[-1] != "Error":
        return None
    for child in root:
        if child.tag.rsplit("}", 1)[-1] == "Code":
            value = (child.text or "").strip()
            return value if value in SAFE_S3_ERROR_CODES else None
    return None


def public_items(directory, manifest, manifest_sha256, prefix):
    """The signing side reads only the public delivery package and does not need the upload machine's absolute-path mapping.
    签名端只读取公开交付包，不需要上传机的绝对路径映射。
    """
    assets = {}
    for asset in manifest["assets"]:
        assets.setdefault(asset["object_key"], asset)
    result = []
    for key, asset in sorted(assets.items()):
        result.append({"source_key": key, "kind": "asset", "object_key": "/".join(filter(None, (prefix, key))),
                       "sha256": asset["sha256"], "byte_size": asset["byte_size"],
                       "content_type": asset.get("media_type") or "application/octet-stream"})
    base = "/".join(filter(None, (prefix, "batches", manifest["batch_id"], manifest_sha256)))
    for relative, value in sorted(manifest["files"].items()):
        media_type = ("application/json" if relative.endswith(".json") else "application/sql" if relative.endswith(".sql")
                      else "application/gzip" if relative.endswith(".gz") else "application/octet-stream")
        result.append({"source_key": relative, "kind": value["kind"], "object_key": base + "/" + relative,
                       "sha256": value["sha256"], "byte_size": value["byte_size"],
                       "content_type": media_type})
    result.append({"source_key": "manifest.json", "kind": "manifest", "object_key": base + "/manifest.json",
                   "sha256": manifest_sha256, "byte_size": (directory / "manifest.json").stat().st_size,
                   "content_type": "application/json"})
    return result


def checked_url(value, bucket, region, key):
    from _batch_files import require
    require(isinstance(value, str) and not any(char in value for char in "\r\n"), 'Invalid signed URL format')
    parsed = urlsplit(value)
    hosts = {f"{bucket}.s3.{region}.amazonaws.com"}
    if region == "us-east-1":
        hosts.add(f"{bucket}.s3.amazonaws.com")
    try:
        valid = (parsed.scheme == "https" and parsed.hostname in hosts and parsed.port in (None, 443)
                 and not parsed.username and not parsed.password and not parsed.fragment
                 and unquote(parsed.path) == "/" + key and bool(parsed.query))
    except ValueError:
        valid = False
    require(valid, 'The signed URL does not belong to an explicitly selected S3 object in this batch')
    return parsed


def sign_upload_urls(directory, bucket, region, prefix, output, *, expires_seconds=21600,
                     expected_account=None, s3_client=None, sts_client=None):
    from _batch_files import load_export, read_json, require, write_json
    directory, output = Path(directory).resolve(), Path(output).resolve()
    manifest, manifest_sha256 = load_export(directory)
    prefix = clean_prefix(prefix)
    items = public_items(directory, manifest, manifest_sha256, prefix)
    require(21600 <= expires_seconds <= 43200, 'Signature lifetime must be 6 to 12 hours and is also limited by the cloud session expiry')
    require(all(item["byte_size"] <= SINGLE_PUT_LIMIT for item in items), 'This batch contains a file larger than 5 GiB; use the standard SDK upload path')
    protected = {directory / relative for relative in manifest["files"]}
    protected.update(directory / name for name in ("manifest.json", "local_sources.json", "upload_receipt.json", "merge_receipt.json", "cleanup_report.json"))
    require(output not in {path.resolve() for path in protected}
            and not any(output.is_relative_to(directory / name) for name in ("tables", "schema", "evidence")),
            'The private signature file must not overwrite delivery files, path mappings, or receipts')
    identity = {"format": "etl4-batch-signed-upload-v1", "batch_id": manifest["batch_id"],
                "manifest_sha256": manifest_sha256, "bucket": bucket, "region": region, "prefix": prefix}
    if output.exists():
        previous = read_json(output)
        require(all(previous.get(key) == value for key, value in identity.items()), 'The signature output already contains other content; choose another private output path')
    if s3_client is None:
        try:
            import boto3
            from botocore.config import Config
        except ImportError as exc:
            raise ValueError('Cloud signing requires boto3; use an authenticated CloudShell environment') from exc
        session = boto3.Session(region_name=region)
        credentials = session.get_credentials()
        require(credentials is not None, 'CloudShell has no usable AWS session')
        frozen = credentials.get_frozen_credentials()
        # Use the current in-memory session for the entire signature batch instead of checking or refreshing credentials for every URL. / 一批签名共用内存中的当前会话，避免每个 URL 都检查或刷新凭据。
        session_keys = {"aws_access_key_id": frozen.access_key, "aws_secret_access_key": frozen.secret_key,
                        "aws_session_token": frozen.token}
        s3_client = session.client("s3", config=Config(signature_version="s3v4", s3={"addressing_style": "virtual"}), **session_keys)
        if expected_account:
            sts_client = session.client("sts", **session_keys)
    if expected_account:
        require(sts_client is not None and sts_client.get_caller_identity()["Account"] == expected_account,
                'The cloud AWS account does not match the explicit target')
    location = s3_client.get_bucket_location(Bucket=bucket).get("LocationConstraint")
    actual_region = "us-east-1" if location is None else "eu-west-1" if location == "EU" else location
    require(actual_region == region, 'The S3 bucket region does not match the argument')
    blocked = s3_client.get_public_access_block(Bucket=bucket).get("PublicAccessBlockConfiguration", {})
    require(all(blocked.get(key) is True for key in ("BlockPublicAcls", "IgnorePublicAcls", "BlockPublicPolicy", "RestrictPublicBuckets")),
            'S3 Block Public Access is not fully enabled for the bucket')
    signed = []
    for item in items:
        checksum = base64.b64encode(bytes.fromhex(item["sha256"])).decode("ascii")
        put_url = s3_client.generate_presigned_url("put_object", Params={"Bucket": bucket, "Key": item["object_key"],
            "ChecksumSHA256": checksum, "IfNoneMatch": "*", "ContentType": item["content_type"]},
            ExpiresIn=expires_seconds, HttpMethod="PUT")
        head_url = s3_client.generate_presigned_url("head_object", Params={"Bucket": bucket, "Key": item["object_key"],
            "ChecksumMode": "ENABLED"}, ExpiresIn=expires_seconds, HttpMethod="HEAD")
        checked_url(put_url, bucket, region, item["object_key"])
        checked_url(head_url, bucket, region, item["object_key"])
        signed.append({**item, "put_url": put_url, "head_url": head_url})
    result = {**identity, "expires_seconds": expires_seconds, "created_at": datetime.now(timezone.utc).isoformat(), "objects": signed}
    write_json(output, result)
    # New files are readable only by the current account on POSIX; Windows retains the current directory ACL. / 新文件在 POSIX 环境只供当前账号读取；Windows 继续使用当前目录 ACL。
    if sys.platform != "win32":
        output.chmod(0o600)
    return {key: value for key, value in result.items() if key != "objects"} | {"object_count": len(signed)}


class SignedRequestError(Exception):
    def __init__(self, status, aws_code=None):
        self.status = int(status)
        self.aws_code = aws_code if aws_code in SAFE_S3_ERROR_CODES else None
        self.response = {"Error": {"Code": self.aws_code or str(self.status)},
                         "ResponseMetadata": {"HTTPStatusCode": self.status}}
        code_text = f", {self.aws_code}" if self.aws_code else ""
        advice = '; regenerate the temporary signatures' if self.aws_code in {"ExpiredToken", "InvalidToken", "RequestExpired", "TokenRefreshRequired"} else ""
        super().__init__(f"S3 request failed (HTTP {self.status}{code_text}){advice}")


class SignedS3Client:
    """Keep only this batch's short-lived URLs; omit URLs and temporary credentials from HTTP errors and receipts.
    只持有本批短期 URL，HTTP 错误和回执均不包含 URL 或临时凭据。
    """

    def __init__(self, document, expected, identity):
        from _batch_files import require
        require(all(document.get(key) == value for key, value in identity.items())
                and document.get("format") == "etl4-batch-signed-upload-v1", 'The signature file does not match this batch or upload target')
        rows = document.get("objects")
        require(isinstance(rows, list) and len(rows) == len(expected), 'The signature file does not cover all objects in this batch')
        by_key = {item["object_key"]: item for item in expected}
        self.objects = {}
        self.bucket, self.region = identity["bucket"], identity["region"]
        for row in rows:
            key = row.get("object_key")
            require(key in by_key and key not in self.objects and all(row.get(name) == value for name, value in by_key[key].items()),
                    "A signed object's identity, size, or scope does not match")
            checked_url(row.get("put_url"), self.bucket, self.region, key)
            checked_url(row.get("head_url"), self.bucket, self.region, key)
            require(row["byte_size"] <= SINGLE_PUT_LIMIT, 'Files larger than 5 GiB require standard SDK multipart upload')
            self.objects[key] = row
        self._ssl_context = ssl.create_default_context()
        self._local = threading.local()
        self._connections = set()
        self._connection_lock = threading.Lock()

    def connection(self, host):
        # Reuse connections serially within each worker thread; do not share sockets between threads. / 同一工作线程串行复用连接；不同线程不共用 socket。
        current = getattr(self._local, "connection", None)
        if current is not None and self._local.host != host:
            self.discard_connection(current)
            current = None
        if current is None:
            current = http.client.HTTPSConnection(host, timeout=120, context=self._ssl_context)
            self._local.connection, self._local.host = current, host
            with self._connection_lock:
                self._connections.add(current)
        return current

    def discard_connection(self, connection):
        if getattr(self._local, "connection", None) is connection:
            self._local.connection = None
        with self._connection_lock:
            self._connections.discard(connection)
        connection.close()

    def close(self):
        # After all workers finish, the main thread closes all remaining keep-alive connections. / 主线程等全部工作线程结束后，统一释放仍在保活的连接。
        with self._connection_lock:
            connections = list(self._connections)
            self._connections.clear()
        for connection in connections:
            connection.close()
        self._local.connection = None

    def request(self, method, row, headers, body=None):
        start = body.tell() if body is not None else None
        for attempt in range(3):
            if body is not None:
                body.seek(start)
            try:
                return self.request_once(method, row, headers, body)
            except SignedRequestError as exc:
                # Conditional writes can be retried safely; do not retry 403, and let the shared upload function handle 412 with another HEAD request. / 条件写可安全重试；403 不重试，412 交给共享上传函数重新 HEAD。
                if exc.status not in (429, 500, 502, 503, 504) or attempt == 2:
                    raise
            except (OSError, http.client.HTTPException):
                if attempt == 2:
                    raise RuntimeError('The S3 temporary-signature connection failed after 3 attempts; keep the receipt and retry') from None
            time.sleep(attempt + 1)

    def request_once(self, method, row, headers, body=None):
        parsed = checked_url(row["head_url" if method == "HEAD" else "put_url"], self.bucket, self.region, row["object_key"])
        connection = None
        reusable = False
        try:
            connection = self.connection(parsed.hostname)
            connection.putrequest(method, parsed.path + "?" + parsed.query)
            for key, value in headers.items():
                connection.putheader(key, value)
            if method == "PUT":
                connection.putheader("Content-Length", str(row["byte_size"]))
            connection.endheaders()
            if body is not None:
                remaining = row["byte_size"]
                while remaining:
                    block = body.read(min(1024**2, remaining))
                    if not block:
                        raise ValueError('The local file length changed during upload')
                    connection.send(block)
                    remaining -= len(block)
                if body.read(1):
                    raise ValueError('The local file length changed during upload')
            response = connection.getresponse()
            payload = response.read(65536)
            extra = response.read(65536)
            if extra:
                payload = b""  # Skip error-code parsing for oversized responses, but drain them before reusing the connection. / 过长响应不参与错误码解析，仍须读完才能复用连接。
                while extra:
                    extra = response.read(65536)
            response_headers = {key.lower(): value for key, value in response.getheaders()}
            reusable = (not response.will_close and response_headers.get("connection", "").lower() != "close"
                        and response.status in (200, 404, 412))
            if response.status != 200:
                raise SignedRequestError(response.status, safe_s3_error_code(payload))
            return response_headers
        finally:
            if connection is not None and not reusable:
                self.discard_connection(connection)

    def head_object(self, **args):
        row = self.objects[args["Key"]]
        if args["Bucket"] != self.bucket or args.get("ChecksumMode") != "ENABLED":
            raise ValueError("The HEAD request is outside this batch's signed scope")
        headers = self.request("HEAD", row, {"x-amz-checksum-mode": "ENABLED"})
        result = {"ContentLength": int(headers["content-length"]), "ChecksumType": headers.get("x-amz-checksum-type"),
                  "Metadata": {"source-sha256": headers["x-amz-meta-source-sha256"]} if "x-amz-meta-source-sha256" in headers else {},
                  "ETag": headers.get("etag"), "VersionId": headers.get("x-amz-version-id")}
        for field, header in (("ChecksumSHA256", "x-amz-checksum-sha256"), ("ChecksumCRC32", "x-amz-checksum-crc32")):
            if header in headers:
                result[field] = headers[header]
        return result

    def put_object(self, **args):
        row = self.objects[args["Key"]]
        if args["Bucket"] != self.bucket or args["ContentLength"] != row["byte_size"] or args.get("IfNoneMatch") != "*":
            raise ValueError("The PUT request is outside this batch's signed scope")
        # The lower layer already checked SHA256 and CRC32; temporary signatures use S3 full-object SHA256 to receive the same bytes. / 低层已扫描 SHA256+CRC32；临时签名使用 S3 原生完整 SHA256 来接收同一份字节。
        checksum = base64.b64encode(bytes.fromhex(row["sha256"])).decode("ascii")
        self.request("PUT", row, {"Content-Type": row["content_type"], "x-amz-checksum-sha256": checksum,
                                 "If-None-Match": "*"}, args["Body"])
        return {}


def clean_prefix(value):
    prefix = value.strip("/")
    if "\\" in prefix or any(part in ("", ".", "..") for part in prefix.split("/")):
        if prefix:
            raise ValueError('The S3 prefix must use ordinary directory names without empty segments, backslashes, or ..')
    return prefix


def transfer_items(directory, manifest, manifest_sha256, sources, prefix):
    """Deduplicate assets by content and place manifest files in a batch-specific directory.
    资产按内容去重；清单文件放入本批专属目录。
    """
    items = []
    for key, source in sorted(sources.items()):
        items.append({"path": Path(source["path"]), "source_key": key, "kind": "asset",
                      "object_key": "/".join(filter(None, (prefix, key))),
                      "sha256": source["sha256"], "byte_size": source["byte_size"]})
    batch_prefix = "/".join(filter(None, (prefix, "batches", manifest["batch_id"], manifest_sha256)))
    for relative, row in sorted(manifest["files"].items()):
        items.append({"path": directory / relative, "source_key": relative, "kind": row["kind"],
                      "object_key": batch_prefix + "/" + relative,
                      "sha256": row["sha256"], "byte_size": row["byte_size"]})
    items.append({"path": directory / "manifest.json", "source_key": "manifest.json", "kind": "manifest",
                  "object_key": batch_prefix + "/manifest.json", "sha256": manifest_sha256,
                  "byte_size": (directory / "manifest.json").stat().st_size})
    keys = [item["object_key"] for item in items]
    if len(keys) != len(set(keys)):
        raise ValueError('This batch contains duplicate upload object keys')
    return items


def make_progress(index, total, key):
    last_percent = [-10]

    def progress(sent, size):
        percent = min(100, int(sent * 100 / size)) if size else 100
        if percent >= last_percent[0] + 10:
            print(f"[{index}/{total}] file read/transfer {percent}%: {key}", flush=True)
            last_percent[0] = percent

    return progress


def upload_batch(directory, bucket, region, prefix="etl4", receipt_path=None, *, client=None, signed_urls=None, workers=1):
    # Load the SDK only for actual uploads; --help and offline tests do not require boto3. / SDK 仅在实际上传时载入，--help 和离线测试不需要安装 boto3。
    from _batch_files import load_export, load_sources, read_json, require, write_json

    directory = Path(directory).resolve()
    require(type(workers) is int and 1 <= workers <= 16, 'Upload concurrency must be between 1 and 16')
    manifest, manifest_sha256 = load_export(directory)
    sources = load_sources(directory, manifest, manifest_sha256)
    prefix = clean_prefix(prefix)
    items = transfer_items(directory, manifest, manifest_sha256, sources, prefix)
    receipt_path = Path(receipt_path).resolve() if receipt_path else directory / "upload_receipt.json"
    protected = {item["path"].resolve() for item in items}
    protected.add((directory / "local_sources.json").resolve())
    if signed_urls is not None:
        signed_path = Path(signed_urls).resolve()
        require(signed_path not in protected, 'The private signature file must not be a delivery file or original asset')
        protected.add(signed_path)
    if receipt_path in protected or receipt_path == directory:
        raise ValueError('The upload receipt must not overwrite sealed files or original assets')
    receipt_identity = {"format": "etl4-batch-upload-v1", "batch_id": manifest["batch_id"],
                        "manifest_sha256": manifest_sha256, "bucket": bucket,
                        "region": region, "prefix": prefix}
    if receipt_path.exists():
        previous = read_json(receipt_path)
        if any(previous.get(key) != value for key, value in receipt_identity.items()):
            raise ValueError('The existing receipt belongs to another batch or upload target; choose another --receipt path')
    if signed_urls is not None:
        require(client is None, 'Temporary signatures and an SDK client cannot both be specified')
        expected = public_items(directory, manifest, manifest_sha256, prefix)
        identity = {key: value for key, value in receipt_identity.items() if key != "format"}
        client = SignedS3Client(read_json(signed_path), expected, identity)
    elif client is None:
        try:
            import boto3
        except ImportError as exc:
            raise ValueError('Uploading requires boto3; install the AWS upload dependencies first') from exc
        client = boto3.client("s3", region_name=region)
        _s3_upload.check_upload_sdk(client)
    receipt = {**receipt_identity, "status": "partial",
               "expected_objects": len(items), "objects": [],
               "workers": workers,
               "resume_scope": "completed_objects; interrupted_file_restarts",
               "started_at": datetime.now(timezone.utc).isoformat()}
    write_json(receipt_path, receipt)

    def transfer(index, item):
        transfer_options = {"multipart_threshold": SINGLE_PUT_LIMIT + 1} if signed_urls is not None else {}
        result = _s3_upload.upload_object(client, item["path"], bucket, item["object_key"],
                                   item["sha256"], item["byte_size"],
                                   progress=make_progress(index, len(items), item["object_key"]), **transfer_options)
        return {**result, "source_key": item["source_key"], "kind": item["kind"]}

    def save_completed(result):
        # Write receipts only in the main thread so concurrent uploads do not compete to update the progress file. / 只在主线程写回执；并发上传不会竞争修改进度文件。
        receipt["objects"].append(result)
        receipt["completed_objects"] = len(receipt["objects"])
        write_json(receipt_path, receipt)
        action = 'reused' if result["reused"] else 'uploaded'
        print(f"[{len(receipt['objects'])}/{len(items)}] {action} and verified: {result['object_key']}", flush=True)

    try:
        if workers == 1:
            for index, item in enumerate(items, 1):
                receipt["current_object"] = item["object_key"]
                write_json(receipt_path, receipt)
                save_completed(transfer(index, item))
        else:
            first_error = None
            with ThreadPoolExecutor(max_workers=workers) as pool:
                future_items = {pool.submit(transfer, index, item): item for index, item in enumerate(items[:-1], 1)}
                futures = list(future_items)
                try:
                    for future in as_completed(futures):
                        if future.cancelled():
                            continue
                        try:
                            result = future.result()
                        except Exception as exc:
                            if first_error is None:
                                first_error = exc
                                receipt["failed_object"] = future_items[future]["object_key"]
                                for pending in futures:
                                    pending.cancel()
                        else:
                            save_completed(result)
                except BaseException:
                    for pending in futures:
                        pending.cancel()
                    raise
            if first_error is not None:
                raise first_error
            # The manifest represents the complete package; upload it separately only after all other objects succeed. / manifest 代表完整包；其余对象全部成功后才单独上传。
            receipt["current_object"] = items[-1]["object_key"]
            write_json(receipt_path, receipt)
            save_completed(transfer(len(items), items[-1]))
    except BaseException as exc:
        receipt["error_type"] = type(exc).__name__
        if isinstance(exc, SignedRequestError):
            receipt["http_status"] = exc.status
            receipt["aws_error_code"] = exc.aws_code
            receipt.setdefault("failed_object", receipt.get("current_object"))
        write_json(receipt_path, receipt)
        raise
    finally:
        if isinstance(client, SignedS3Client):
            client.close()
    receipt.pop("current_object", None)
    receipt.update(status="complete", completed_at=datetime.now(timezone.utc).isoformat())
    write_json(receipt_path, receipt)
    return receipt


def main():
    parser = StepParser(description=__doc__.splitlines()[0])
    parser.add_argument("--export-dir", type=Path, required=True, help='Export directory from step 38')
    parser.add_argument("--bucket", required=True, help='Target S3 bucket')
    parser.add_argument("--region", required=True, help='Target AWS region')
    parser.add_argument("--prefix", default="etl4", help='S3 object prefix')
    parser.add_argument("--receipt", type=Path, help='Progress receipt; defaults to export-directory/upload_receipt.json')
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--sign-urls", type=Path, help="Generate this batch's private short-lived signature file in authenticated CloudShell")
    modes.add_argument("--signed-urls", type=Path, help='Upload locally using the downloaded private signature file without AWS keys')
    parser.add_argument("--expires-seconds", type=int, default=21600, help='Signature lifetime, 6 to 12 hours, limited by the session expiry')
    parser.add_argument("--expected-account", help='Check the AWS account ID before signing')
    parser.add_argument("--workers", type=int, choices=range(1, 17), default=1, help='Number of concurrent file uploads, default 1; the manifest is always uploaded last')
    args = parser.parse_args()
    configure(args)
    if args.sign_urls:
        result = sign_upload_urls(args.export_dir, args.bucket, args.region, args.prefix, args.sign_urls,
                                  expires_seconds=args.expires_seconds, expected_account=args.expected_account)
        print(f"Generated private short-lived signatures for {result['object_count']} objects; do not include or share the signature file in the delivery package")
        return
    result = upload_batch(args.export_dir, args.bucket, args.region, args.prefix, args.receipt,
                          signed_urls=args.signed_urls, workers=args.workers)
    print(f"Batch upload complete: {result['completed_objects']} objects; receipt status complete")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        # Do not log full SDK, connection, or authorization exceptions because they may contain sensitive request information. / 不把 SDK、连接或授权异常全文写入日志，避免包含敏感请求信息。
        print(str(exc) if isinstance(exc, (ValueError, SignedRequestError)) else f"Upload incomplete: {type(exc).__name__}", file=sys.stderr)
        raise SystemExit(1)
