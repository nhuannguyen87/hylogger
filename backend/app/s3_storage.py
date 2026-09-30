"""Verified read-only access to ETL4 assets stored in S3."""

from collections import OrderedDict
import hashlib
import re
from threading import RLock


def require(condition, message):
    if not condition:
        raise ValueError(message)


class S3Store:
    def __init__(
        self,
        client,
        bucket,
        root_key,
        prefix,
        *,
        cache_bytes=128 * 1024**2,
        object_limit=64 * 1024**2,
        block_limit=16 * 1024**2,
    ):
        self.client = client
        self.bucket = bucket
        self.root_key = root_key
        self.prefix = prefix.strip("/") + "/"

        require(
            bool(prefix.strip("/")),
            "S3 prefix is required",
        )

        self.cache_bytes = cache_bytes
        self.object_limit = object_limit
        self.block_limit = block_limit

        self._cache = OrderedDict()
        self._bytes = 0
        self._lock = RLock()

    def asset(self, conn, asset_id):
        rows = conn.execute(
            """
            SELECT
                a.*,
                l.root_key,
                l.object_key,
                l.access_status
            FROM core.asset a
            JOIN core.asset_location l
                ON l.asset_id = a.id
               AND l.verified_sha256 = a.sha256
            WHERE a.id = %s
              AND l.backend = 's3'
              AND l.access_status = 'verified_remote'
              AND l.root_key = %s
            ORDER BY l.object_key
            """,
            (asset_id, self.root_key),
        ).fetchall()

        if not rows:
            return None

        require(
            len(rows) == 1,
            "Ambiguous S3 asset location",
        )

        row = rows[0]
        key = row["object_key"]

        require(
            key.startswith(self.prefix)
            and not any(
                part in (".", "..", "")
                for part in key.split("/")
            )
            and "\\" not in key
            and ":" not in key,
            "S3 object is outside configured release prefix",
        )

        require(
            bool(re.fullmatch(r"[0-9a-f]{64}", row["sha256"])),
            "Invalid asset SHA256",
        )

        return row

    def _read(
        self,
        asset,
        offset=None,
        length=None,
        checksum=None,
    ):
        ranged = offset is not None

        expected = (
            length
            if ranged
            else asset["byte_size"]
        )

        digest = (
            checksum
            if ranged
            else asset["sha256"]
        )

        limit = (
            self.block_limit
            if ranged
            else self.object_limit
        )

        require(
            isinstance(expected, int)
            and 0 < expected <= limit,
            "Asset exceeds permitted read limit",
        )

        if ranged:
            require(
                offset >= 0
                and offset + expected <= asset["byte_size"],
                "Invalid S3 byte range",
            )

        cache_key = (
            asset["object_key"],
            asset["sha256"],
            offset,
            expected,
            digest,
        )

        with self._lock:
            if cache_key in self._cache:
                self._cache.move_to_end(cache_key)
                return self._cache[cache_key]

            options = {
                "Bucket": self.bucket,
                "Key": asset["object_key"],
            }

            if ranged:
                options["Range"] = (
                    f"bytes={offset}-{offset + expected - 1}"
                )

            response = self.client.get_object(**options)

            with response["Body"] as body:
                require(
                    response.get("ContentLength") == expected,
                    "Remote asset size mismatch",
                )

                if ranged:
                    require(
                        response.get(
                            "ResponseMetadata",
                            {},
                        ).get("HTTPStatusCode") == 206
                        and response.get("ContentRange")
                        == (
                            f"bytes {offset}-"
                            f"{offset + expected - 1}/"
                            f'{asset["byte_size"]}'
                        ),
                        "Remote byte range mismatch",
                    )

                data = body.read(expected + 1)

                require(
                    len(data) == expected
                    and hashlib.sha256(data).hexdigest()
                    == digest,
                    "Remote asset checksum mismatch",
                )

            if len(data) <= self.cache_bytes:
                while (
                    self._bytes + len(data)
                    > self.cache_bytes
                    and self._cache
                ):
                    _, old = self._cache.popitem(
                        last=False
                    )
                    self._bytes -= len(old)

                self._cache[cache_key] = data
                self._bytes += len(data)

            return data

    def full(self, asset):
        require(
            not asset["object_key"]
            .lower()
            .endswith(".f32"),
            "Whole spectral F32 reads are forbidden",
        )

        return self._read(asset)

    def block(self, asset, block):
        return self._read(
            asset,
            offset=block["byte_offset"],
            length=block["byte_length"],
            checksum=block["sha256"],
        )