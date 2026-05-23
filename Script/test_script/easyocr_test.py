import EasyOCR_1_7_2.easyocr as easyocr
import time
OCRmodel_path = "/EasyOCR_1_7_2/models"
reader = easyocr.Reader(lang_list=['ch_sim', 'en'],
                        model_storage_directory=OCRmodel_path,
                        download_enabled=False)  # 这只需要运行一次即可将模型加载到内存中
start = time.time()
result = reader.readtext('D:\Github\GameVideoEdit\Video\chinese.jpg',detail=0)
end = time.time()
print(f"gap_time{end-start}s\n",result)