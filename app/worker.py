"""One isolated DOCX operation. DOCX and report bytes travel through anonymous pipes."""
import argparse
import ctypes
import logging
import math
import os
from io import BytesIO
import json
from pathlib import PurePosixPath
import signal
import sys
from zipfile import BadZipFile, ZipFile

from formal_check import CheckError, DocumentReadError, check_document
from formal_fix.service import FixError, fix_bytes

from formal_check.coverage import LIMITATIONS
from formal_check import __version__


def validate_package(path):
    try:
        with ZipFile(path) as package:
            entries = package.infolist()
            names = [entry.filename for entry in entries]
            if len(entries) > 2048 or len(set(names)) != len(names):
                raise DocumentReadError("Слишком много частей DOCX или найдены повторяющиеся части.")
            if not {"[Content_Types].xml", "word/document.xml", "_rels/.rels"}.issubset(names):
                raise DocumentReadError("Файл не является поддерживаемым DOCX.")
            if sum(entry.file_size for entry in entries) > 256 * 1024 * 1024:
                raise DocumentReadError("Распакованный DOCX превышает 256 МиБ.")
            for entry in entries:
                name = PurePosixPath(entry.filename)
                if name.is_absolute() or ".." in name.parts or "\\" in entry.filename or entry.flag_bits & 1:
                    raise DocumentReadError("Неподдерживаемая структура архива DOCX.")
                if entry.file_size > 64 * 1024 * 1024 or (entry.filename.endswith(".xml") and entry.file_size > 32 * 1024 * 1024):
                    raise DocumentReadError("Одна из частей DOCX превышает допустимый размер.")
                if entry.file_size > 1024 * 1024 and entry.file_size / max(entry.compress_size, 1) > 1000:
                    raise DocumentReadError("Слишком высокая степень сжатия DOCX.")
                if entry.filename.lower().endswith("vbaproject.bin"):
                    raise DocumentReadError("Документы с макросами не поддерживаются.")
    except (BadZipFile, OSError, NotImplementedError) as exc:
        raise DocumentReadError("Не удалось открыть архив DOCX.") from exc


def prepare_process(parent_pid, memory_mb, timeout):
    if sys.platform == "linux":
        # A crashed API must not leave a document worker running indefinitely.
        ctypes.CDLL(None).prctl(1, signal.SIGKILL)
        if os.getppid() != parent_pid:
            raise SystemExit(1)
        import resource
        memory = memory_mb * 1024 * 1024
        resource.setrlimit(resource.RLIMIT_AS, (memory, memory))
        cpu = math.ceil(timeout) + 1
        resource.setrlimit(resource.RLIMIT_CPU, (cpu, cpu + 1))


def run_operation(source, operation, ruleset):
    warnings = []

    class Capture(logging.Handler):
        def emit(self, record):
            message = record.getMessage()
            if (message.startswith(("READER:", "IMAGES:", "FORMULAS:", "TABLES:")) or record.levelno >= logging.WARNING) and message not in warnings and len(warnings) < 100:
                warnings.append(message)

    logger = logging.getLogger("formal_check")
    logger.setLevel(logging.INFO)
    capture = Capture()
    logger.addHandler(capture)
    try:
        validate_package(BytesIO(source))
        corrected = fix_bytes(source) if operation == "fix" else None
        report = check_document(BytesIO(corrected if corrected is not None else source))
        return {
            "state": "fixed" if corrected is not None else "checked",
            "current_version": "corrected" if corrected is not None else "original",
            "checker_version": __version__, "ruleset_version": ruleset,
            "report": report, "warnings": warnings,
            "limitations": [{"rule_id": key, "description": value} for key, value in LIMITATIONS.items()],
        }, corrected
    finally:
        logger.removeHandler(capture)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("operation", choices=("check", "fix"))
    parser.add_argument("--ruleset", required=True)
    parser.add_argument("--parent-pid", type=int, required=True)
    parser.add_argument("--memory-mb", type=int, required=True)
    parser.add_argument("--timeout", type=float, required=True)
    args = parser.parse_args()
    prepare_process(args.parent_pid, args.memory_mb, args.timeout)
    try:
        data, corrected = run_operation(sys.stdin.buffer.read(), args.operation, args.ruleset)
        metadata = json.dumps(data, ensure_ascii=False, allow_nan=False).encode()
        if len(metadata) + len(corrected or b"") > 64 * 1024 * 1024:
            raise SystemExit(4)
        # One JSON line followed by the optional binary DOCX.
        sys.stdout.buffer.write(metadata + b"\n")
        if corrected is not None:
            sys.stdout.buffer.write(corrected)
    except DocumentReadError:
        raise SystemExit(2)
    except (CheckError, FixError, MemoryError):
        raise SystemExit(3)


if __name__ == "__main__":
    main()
