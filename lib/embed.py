"""CPU dense-embedding wrapper (onnxruntime + tokenizers, no torch).

    from lib.embed import Embedder, save_shard, load_shards, topk
    emb = Embedder()                       # default: multilingual-e5-small, fused fp32, max_len 256, 3 threads
    P = emb.encode(chunk_texts, kind="passage")   # float32 [n, 384], L2-normalised
    q = emb.encode(["ακύρωση απόλυσης"], kind="query")[0]

Model files live in search/models/<key>/ (tokenizer.json + onnx/model[_quantized].onnx), downloaded
from the Xenova/* ONNX exports of the original checkpoints; e5-small/onnx/model_opt.onnx is the same fp32
model with attention/LayerNorm/GELU fused by onnxruntime.transformers.optimizer (bit-identical vectors,
~15% faster). See notes/embed.md for the benchmarks.

Vectors: one float16 .npy shard per ~100k chunks (save_shard/load_shards). Search:
  DenseIndex(load_shards(paths)).search(q, k)   full-corpus brute force (ORT Cast+MatMul, ~0.4 s @ 2.5M rows)
  DenseIndex(..., int8=True)                     int8 copy in RAM (ORT MatMulInteger, ~0.12 s @ 2.5M, 0.96 GB)
  rescore(mats, q, row_ids)                       dense score of BM25 candidates (cheap, mmap is fine)
"""
import os
import re

os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")  # keep tokenization on the calling thread

import numpy as np

MODELS_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "models")

# key -> settings. prefix: (query_prefix, passage_prefix). pooling: "mean" | "cls".
REGISTRY = {
    "e5-small": dict(hf="intfloat/multilingual-e5-small", prefix=("query: ", "passage: "), pooling="mean", max_len=256),
    "e5-base": dict(hf="intfloat/multilingual-e5-base", prefix=("query: ", "passage: "), pooling="mean", max_len=256),
    "minilm-l12": dict(hf="sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2", prefix=("", ""), pooling="mean", max_len=128),
    "bge-m3": dict(hf="BAAI/bge-m3", prefix=("", ""), pooling="cls", max_len=512),
}
DEFAULT = "e5-small"
_WS = re.compile(r"\s+")


class Embedder:
    def __init__(self, name_or_dir=None, threads=3, max_len=None, quantized=False, onnx_file=None):
        import onnxruntime as ort
        from tokenizers import Tokenizer

        name_or_dir = name_or_dir or DEFAULT
        if os.path.isdir(name_or_dir):
            mdir = os.path.abspath(name_or_dir)
            key = os.path.basename(mdir.rstrip("/"))
        else:
            key = name_or_dir
            mdir = os.path.join(MODELS_DIR, key)
        cfg = REGISTRY.get(key, dict(hf=key, prefix=("", ""), pooling="mean", max_len=256))
        self.key = key
        if onnx_file is None and not quantized and os.path.exists(os.path.join(mdir, "onnx", "model_opt.onnx")):
            onnx_file = "model_opt.onnx"
        self.name = cfg["hf"] + (" (int8)" if quantized and not onnx_file else "")
        self.prefix = cfg["prefix"]
        self.pooling = cfg["pooling"]
        self.max_len = int(max_len or cfg["max_len"])

        if onnx_file is None:
            onnx_file = "model_quantized.onnx" if quantized else "model.onnx"
        path = onnx_file if os.path.isabs(onnx_file) else os.path.join(mdir, "onnx", onnx_file)
        so = ort.SessionOptions()
        so.intra_op_num_threads = int(threads)
        so.inter_op_num_threads = 1
        so.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
        so.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
        so.enable_cpu_mem_arena = False  # variable shapes; arena would keep growing
        self.sess = ort.InferenceSession(path, sess_options=so, providers=["CPUExecutionProvider"])
        self.input_names = [i.name for i in self.sess.get_inputs()]
        self.dim = int(self.sess.get_outputs()[0].shape[-1])

        tok = Tokenizer.from_file(os.path.join(mdir, "tokenizer.json"))
        tok.no_padding()
        tok.enable_truncation(max_length=self.max_len)
        self.tok = tok
        self.pad_id = tok.token_to_id("<pad>")
        if self.pad_id is None:
            self.pad_id = tok.token_to_id("[PAD]") or 0

    # ---------------------------------------------------------------
    def tokenize(self, texts, kind="passage"):
        pre = self.prefix[0] if kind == "query" else self.prefix[1]
        texts = [pre + _WS.sub(" ", t or "").strip() for t in texts]
        return [e.ids for e in self.tok.encode_batch(texts)]

    def encode(self, texts, kind="passage", batch_size=16):
        """-> float32 [n, dim], L2-normalised. Batches are length-sorted (less padding)."""
        if isinstance(texts, str):
            texts = [texts]
        n = len(texts)
        out = np.zeros((n, self.dim), dtype=np.float32)
        if n == 0:
            return out
        ids = self.tokenize(texts, kind)
        order = sorted(range(n), key=lambda i: len(ids[i]))
        for s in range(0, n, batch_size):
            idx = order[s:s + batch_size]
            L = max(len(ids[i]) for i in idx)
            inp = np.full((len(idx), L), self.pad_id, dtype=np.int64)
            mask = np.zeros((len(idx), L), dtype=np.int64)
            for r, i in enumerate(idx):
                inp[r, :len(ids[i])] = ids[i]
                mask[r, :len(ids[i])] = 1
            feed = {"input_ids": inp, "attention_mask": mask}
            if "token_type_ids" in self.input_names:
                feed["token_type_ids"] = np.zeros_like(inp)
            h = self.sess.run(None, feed)[0]  # [b, L, d]
            if self.pooling == "cls":
                v = h[:, 0, :]
            else:
                m = mask[:, :, None].astype(np.float32)
                v = (h * m).sum(1) / np.maximum(m.sum(1), 1e-9)
            v = v / np.maximum(np.linalg.norm(v, axis=1, keepdims=True), 1e-12)
            out[idx] = v
        return out

    def __repr__(self):
        return f"Embedder({self.name}, dim={self.dim}, max_len={self.max_len})"


def embed_text(locator, text, title=None, max_chars=1500, max_title=100):
    """Text to embed for one chunk: short locator/title header + the chunk body.
    The tokenizer truncates at max_len anyway; max_chars just avoids tokenizing huge strings."""
    if title:
        title = _WS.sub(" ", title).strip()
        if len(title) > max_title:
            title = title[:max_title].rsplit(" ", 1)[0] + "…"
    head = " — ".join(x for x in (title, locator) if x)
    body = _WS.sub(" ", text or "").strip()
    s = (head + ". " + body) if head else body
    return s[:max_chars]


# ------------------------------------------------------------------- vector shards
def save_shard(path, vecs):
    """Save [n, d] vectors as float16 .npy (one big file per shard; NTFS hates small writes)."""
    vecs = np.asarray(vecs, dtype=np.float16)
    tmp = path + ".tmp.npy"
    np.save(tmp, vecs)
    os.replace(tmp, path)


def load_shards(paths, mmap=True):
    """Load shards -> list of float16 arrays (memory-mapped by default; concat yourself if needed)."""
    return [np.load(p, mmap_mode="r" if mmap else None) for p in paths]


INT8_SCALE = 127 / 0.2  # e5-small components |x| < 0.18 on real data; recall@100 vs fp32 = 0.97


def to_int8(vecs, scale=INT8_SCALE):
    return np.clip(np.round(np.asarray(vecs, dtype=np.float32) * scale), -127, 127).astype(np.int8)


def _ort_scorer(dtype, threads):
    import onnxruntime as ort
    from onnx import TensorProto as T, helper as h
    d = "d"
    if dtype == "f16":
        nodes = [h.make_node("Cast", ["M"], ["Mf"], to=T.FLOAT), h.make_node("MatMul", ["Mf", "q"], ["s"])]
        ins = [h.make_tensor_value_info("M", T.FLOAT16, ["n", d]), h.make_tensor_value_info("q", T.FLOAT, [d, 1])]
        out = h.make_tensor_value_info("s", T.FLOAT, ["n", 1])
    else:
        nodes = [h.make_node("MatMulInteger", ["M", "q", "zm", "zq"], ["s"])]
        ins = [h.make_tensor_value_info("M", T.INT8, ["n", d]), h.make_tensor_value_info("q", T.INT8, [d, 1]),
               h.make_tensor_value_info("zm", T.INT8, []), h.make_tensor_value_info("zq", T.INT8, [])]
        out = h.make_tensor_value_info("s", T.INT32, ["n", 1])
    m = h.make_model(h.make_graph(nodes, "score", ins, [out]), opset_imports=[h.make_opsetid("", 17)])
    m.ir_version = 9
    so = ort.SessionOptions()
    so.intra_op_num_threads = int(threads)
    return ort.InferenceSession(m.SerializeToString(), so, providers=["CPUExecutionProvider"])


class DenseIndex:
    """Brute-force cosine search over float16 shards (list of [n_i, d] arrays, mmap ok).
    int8=True keeps an int8 copy in RAM (half the f16 size) and scores with MatMulInteger (~3x faster)."""

    def __init__(self, mats, int8=False, threads=3, block=131072):
        if isinstance(mats, np.ndarray):
            mats = [mats]
        self.block = block
        self.int8 = int8
        if int8:
            self.mats = [np.concatenate([to_int8(M[s:s + block]) for s in range(0, M.shape[0], block)]) for M in mats]
        else:
            self.mats = mats
        self.n = sum(M.shape[0] for M in self.mats)
        try:
            self.sess = _ort_scorer("i8" if int8 else "f16", threads)
        except Exception:  # onnx not importable -> numpy fallback (slower)
            self.sess = None

    def scores(self, q):
        q = np.asarray(q, dtype=np.float32).ravel()
        out = []
        for M in self.mats:
            for s in range(0, M.shape[0], self.block):
                blk = np.ascontiguousarray(M[s:s + self.block])
                if self.sess is None:
                    sc = blk.astype(np.float32) @ (q * (INT8_SCALE if self.int8 else 1))
                elif self.int8:
                    z = np.array(0, np.int8)
                    sc = self.sess.run(None, {"M": blk, "q": to_int8(q)[:, None], "zm": z, "zq": z})[0][:, 0]
                else:
                    sc = self.sess.run(None, {"M": blk, "q": q[:, None]})[0][:, 0]
                out.append(sc.astype(np.float32))
        sc = np.concatenate(out)
        return sc / (INT8_SCALE * INT8_SCALE) if self.int8 else sc

    def search(self, q, k=100):
        """-> (global_row_ids, cosine scores) best first. Row ids count across shards in order."""
        sc = self.scores(q)
        k = min(k, sc.shape[0])
        part = np.argpartition(-sc, k - 1)[:k]
        o = np.argsort(-sc[part])
        return part[o], sc[part][o]


def rescore(mats, q, row_ids):
    """Cosine of q against given global rows (e.g. BM25 top-200) -> float32 [len(row_ids)]."""
    if isinstance(mats, np.ndarray):
        mats = [mats]
    offs = np.cumsum([0] + [M.shape[0] for M in mats])
    q = np.asarray(q, dtype=np.float32).ravel()
    out = np.empty(len(row_ids), dtype=np.float32)
    for j, r in enumerate(row_ids):
        si = int(np.searchsorted(offs, r, side="right") - 1)
        out[j] = np.asarray(mats[si][r - offs[si]], dtype=np.float32) @ q
    return out


def topk(mats, q, k=50):
    """Convenience: brute-force top-k over float16 shard(s)."""
    return DenseIndex(mats).search(q, k)
