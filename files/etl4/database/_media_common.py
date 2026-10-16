"""Read files and calculate coordinates for image and spectral processing steps.
图片和光谱步骤使用的文件读取与坐标计算。
"""

from _db_common import CODE, ETL
from _db_files import digest, inside, read, sha, write
from _settings import settings, raw_path, resolve_reference
from PIL import Image
from _media_manifest import pack_document, unpack_document
import shutil


DIRECTION = {
    "source_depth_direction": "left_to_right",
    "direction_status": "confirmed",
    "direction_basis": "NVCL row order specified by the project: increasing depth from left to right",
}
INDICATOR = {
    "range_basis": "row_sample_interval",
    "endpoint_rule": "first_last_pixel_center",
    "single_sample_rule": "center",
    "coordinate_space": "row_crop_pixels",
}


def require(condition, message):
    if not condition:
        raise ValueError(message)


def confirmed_direction(hole, direction=None):
    """Source rows increase in depth from left to right; retain older arguments only for read compatibility.
    来源每行按孔深从左到右排列；旧参数仅保留读取兼容。
    """
    direction = DIRECTION if direction is None else direction
    require(
        direction.get("direction_status") == "confirmed"
        and direction.get("source_depth_direction") == "left_to_right"
        and bool(direction.get("direction_basis")),
        "Image direction must be confirmed and supported by the current mapping rule",
    )
    return dict(DIRECTION)


def stage(number, work=None):
    work = settings.media_work_dir if work is None else work
    return unpack_document(read(work / f"{number}.json"))


def save_stage(number, value, work=None):
    work = settings.media_work_dir if work is None else work
    write(work / f"{number}.json", pack_document(value))
    print(f'Step {number}: {value.get("status", "prepared")}', flush=True)


def prepare_contract_evidence(evidence_dir):
    """Copy small service format references for a new batch; read large spectra and images from their original paths.
    为新批次复制小型服务格式依据；光谱和图片大文件仍按原路径读取。
    """
    sources = [p for p in (CODE / "media_evidence" / "official").glob("*") if p.is_file()]
    required = {"NVCLDataSvcDao.java", "SpectralDataVo.java", "getspectraldatausage.html"}
    require(required <= {p.name for p in sources}, "Missing official contract evidence")
    for source in sources:
        target = inside(settings.database_dir, evidence_dir / "official" / source.name)
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            require(sha(target) == sha(source), "Official contract evidence changed")
        else:
            shutil.copyfile(source, target)


def units(work=None):
    work = settings.media_work_dir if work is None else work
    return stage(20, work)["datasets"]


def source_folder(u):
    if u.get("prepared_object_key"):
        return resolve_reference(u["prepared_object_key"])
    return inside(settings.prepared_dir, settings.prepared_dir / u["prepared_directory"])


def source_path(u, rel):
    snapshot = inside(source_folder(u), source_folder(u) / "source_metadata" / rel)
    if snapshot.is_file():
        return snapshot
    return raw_path(u["hole_id"], rel)


def marker(sample_no, a, b, width):
    require(
        all(isinstance(v, int) and not isinstance(v, bool) for v in (sample_no, a, b, width)),
        "Integer sample identities and width required",
    )
    require(
        0 <= a <= sample_no <= b and width >= 1,
        "Sample outside mapping interval or invalid image width",
    )
    p = (sample_no - a) / (b - a) if b > a else 0.5
    return {
        "method": "sample_index_linear",
        "version": 1,
        "status": "approximate",
        "p": p,
        "x_px": p * (width - 1),
    }


def decode(path):
    with Image.open(path) as im:
        # 裁图使用解码后的原始坐标；拒绝未经审阅的 EXIF 变换。 / Crop using decoded native coordinates and reject EXIF transformations that have not been reviewed.
        require(
            im.getexif().get(274, 1) == 1, "EXIF orientation requires an explicit layout review"
        )
        return im.convert("RGB")


def media_code_hash():
    files = sorted(
        p
        for p in CODE.glob("*.py")
        if p.name.startswith("_media_")
        or (p.name.split("_")[0].isdigit() and 20 <= int(p.name.split("_")[0]) <= 29)
    )
    return digest({p.name: sha(p) for p in files})
