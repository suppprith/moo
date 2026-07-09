-- SUP-77: persist chunk embeddings as raw float32 BLOBs. The sqlite-vec ANN
-- index (SUP-78) is built from these; embedding_model/_dims already exist on
-- `chunk` so re-embedding on a model change is possible.

ALTER TABLE chunk ADD COLUMN embedding BLOB;
