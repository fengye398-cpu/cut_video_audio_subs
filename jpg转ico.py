from PIL import Image

jpg_path = r"D:\桌面\subtitle_tools\cut_video_audio_subs\yingbiao.jpg"
ico_path = r"D:\桌面\subtitle_tools\cut_video_audio_subs\yingbiao.ico"

img = Image.open(jpg_path)
# 建议转换为 256x256，兼容 Windows 图标
img = img.resize((64, 64), Image.LANCZOS)
img.save(ico_path, format='ICO')
print(f"已生成 {ico_path}")