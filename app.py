from factorylib import create_app
import file_adapter

app = create_app("files", "JSON / CSV / 疑似画像の生成", file_adapter)
