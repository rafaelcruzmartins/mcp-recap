"""Leitura direta dos pontos de arquivos ``.rcs`` (ReCap, contêiner ``ADOCT`` versão 3.x).

O formato não é documentado pela Autodesk. A estrutura abaixo foi levantada com nuvens
sintéticas de conteúdo conhecido, geradas pelo DeCap 27.0.1.242, e conferida ponto a ponto
contra os arquivos de origem (ver ``docs/formato-rcs.md``):

* cabeçalho: ``ADOCT``, versão, translação/rotação/escala do scan e uma tabela de seções
  ``(tipo, deslocamento, tamanho)``;
* seção 2: tabela de folhas do octree (368 bytes cada), com os limites da folha, os limites
  dos dados, o número de pontos e o tamanho do bloco de dados;
* seção 1: os blocos de dados das folhas, na mesma ordem. Cada bloco traz os nós de um
  octree interno (4 bytes por nó) seguidos dos pontos (16 bytes por ponto);
* ponto: X, Y e Z em 18 bits cada, em milímetros a partir do canto mínimo da folha, depois
  intensidade, B, G, R (1 byte cada) e 4 bytes não interpretados.

Os pontos de cada folha são gravados em ordem embaralhada: ler só o início do bloco dá uma
amostra espacialmente uniforme. É isso que permite navegar em nuvens de bilhões de pontos
sem ler o arquivo inteiro.

Nada é escrito: os arquivos são abertos somente para leitura.
"""

from __future__ import annotations

import struct
import threading
from pathlib import Path

import numpy as np

from . import errors

MAGIC = b"ADOCT"
HEADER_BYTES = 0x1000
SECTION_COUNT_OFFSET = 0x129
SECTION_TABLE_OFFSET = 0x12D
SECTION_POINTS = 1
SECTION_LEAVES = 2
MM = 0.001

LEAF = np.dtype([
    ("mn", "<f8", 3), ("mx", "<f8", 3),        # limites da folha (cubo do octree), coordenadas locais do scan
    ("dmn", "<f8", 3), ("dmx", "<f8", 3),      # limites dos pontos dentro da folha
    ("levels", "<u4"), ("np", "<u4"), ("size", "<u4"),
    ("inner", "<u4", 32),                      # nós internos por nível do octree interno
    ("leaf", "<u4", 33),                       # nós-folha (= pontos) por nível
])
POINT = np.dtype([("q", "<u8"), ("i", "u1"), ("b", "u1"), ("g", "u1"), ("r", "u1"), ("x", "<u4")])

assert LEAF.itemsize == 368 and POINT.itemsize == 16


def _unsupported(path: Path, why: str) -> errors.RecapMcpError:
    return errors.RecapMcpError(
        errors.UNSUPPORTED_FORMAT,
        f"'{path.name}' não tem a estrutura de .rcs que este servidor sabe ler ({why}). "
        "Nenhum ponto foi lido, para não mostrar dados errados.", path=str(path))


class RcsFile:
    """Um arquivo .rcs aberto para leitura: cabeçalho, tabela de folhas e leitura de pontos."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self._fh = None
        self._lock = threading.Lock()
        try:
            with self.path.open("rb") as fh:
                head = fh.read(HEADER_BYTES)
                if not head.startswith(MAGIC):
                    raise errors.RecapMcpError(errors.UNSUPPORTED_FORMAT,
                                               f"'{self.path.name}' não tem a assinatura ADOCT de um .rcs.",
                                               path=str(self.path))
                if len(head) < HEADER_BYTES:
                    raise _unsupported(self.path, "cabeçalho truncado")
                self.version = struct.unpack_from("<II", head, 8)
                d = struct.unpack_from("<9d", head, 0x10)
                self.translation = np.array(d[0:3], dtype=np.float64)
                self.rotation_deg = tuple(d[3:6])
                self.scale = tuple(d[6:9])
                self.num_points_header = struct.unpack_from("<Q", head, 0xD8)[0]
                count = struct.unpack_from("<I", head, SECTION_COUNT_OFFSET)[0]
                if not 2 <= count <= 64:
                    raise _unsupported(self.path, f"tabela de seções inesperada ({count} entradas)")
                self.sections: dict[int, tuple[int, int]] = {}
                for k in range(count):
                    kind, off, size = struct.unpack_from("<QQQ", head, SECTION_TABLE_OFFSET + 24 * k)
                    self.sections[int(kind)] = (int(off), int(size))
                if SECTION_POINTS not in self.sections or SECTION_LEAVES not in self.sections:
                    raise _unsupported(self.path, "seções de folhas/pontos ausentes")
                off2, size2 = self.sections[SECTION_LEAVES]
                fh.seek(off2)
                raw_n = fh.read(8)
                n = struct.unpack("<Q", raw_n)[0] if len(raw_n) == 8 else -1
                if n < 0 or size2 != 8 + n * LEAF.itemsize:
                    raise _unsupported(self.path, "tamanho da tabela de folhas não confere")
                buf = fh.read(n * LEAF.itemsize)
                if len(buf) != n * LEAF.itemsize:
                    raise _unsupported(self.path, "tabela de folhas truncada")
                self.leaves = np.frombuffer(buf, LEAF)
        except OSError as exc:
            raise errors.RecapMcpError(errors.PATH_NOT_FOUND, f"Não foi possível ler {self.path}: {exc}") from exc

        size = self.leaves["size"].astype(np.int64)
        nodes = self.leaves["inner"].sum(1, dtype=np.int64) + self.leaves["leaf"].sum(1, dtype=np.int64)
        self.counts = self.leaves["np"].astype(np.int64)
        off1, size1 = self.sections[SECTION_POINTS]
        if n and (int(size.sum()) != size1 or bool(np.any(nodes * 4 + self.counts * POINT.itemsize != size))):
            raise _unsupported(self.path, "blocos de pontos não conferem com a tabela de folhas")
        starts = off1 + np.concatenate(([0], np.cumsum(size)[:-1])) if n else np.zeros(0, np.int64)
        self.point_offsets = (starts + nodes * 4).astype(np.int64)
        self.num_points = int(self.counts.sum())

    def read_points(self, leaf: int, count: int) -> np.ndarray:
        """Primeiros ``count`` pontos da folha (amostra uniforme), como registros ``POINT``."""
        count = int(min(count, self.counts[leaf]))
        if count <= 0:
            return np.zeros(0, POINT)
        with self._lock:
            if self._fh is None:
                self._fh = self.path.open("rb")
            self._fh.seek(int(self.point_offsets[leaf]))
            buf = self._fh.read(count * POINT.itemsize)
        return np.frombuffer(buf, POINT, count=len(buf) // POINT.itemsize)

    def close(self) -> None:
        with self._lock:
            if self._fh is not None:
                self._fh.close()
                self._fh = None


def unpack_xyz(q: np.ndarray) -> np.ndarray:
    """Deslocamentos X, Y, Z (metros, float32) em relação ao canto mínimo da folha."""
    out = np.empty((len(q), 3), np.float32)
    out[:, 0] = (q & 0x3FFFF).astype(np.float32)
    out[:, 1] = ((q >> np.uint64(18)) & 0x3FFFF).astype(np.float32)
    out[:, 2] = ((q >> np.uint64(36)) & 0x3FFFF).astype(np.float32)
    out *= MM
    return out
