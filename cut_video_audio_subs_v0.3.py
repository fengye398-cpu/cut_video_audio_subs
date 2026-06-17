import os
import re
import pysrt
import subprocess
import time
from datetime import timedelta
from glob import glob
from moviepy.editor import VideoFileClip, AudioFileClip
import sys
import threading
import tkinter as tk
from tkinter import filedialog, messagebox
from tkinter import ttk
from moviepy.config import change_settings
import shutil

def ensure_dir(path):
    if not os.path.exists(path):
        os.makedirs(path)

def get_video_duration(path):
    command = [
        "ffprobe", "-v", "error", "-show_entries", "format=duration",
        "-of", "default=nw=1:nk=1", path
    ]
    result = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, creationflags=subprocess.CREATE_NO_WINDOW)
    try:
        return float(result.stdout.strip())
    except Exception:
        return 0.0

def format_timedelta(td):
    total_seconds = int(td.total_seconds())
    hours, remainder = divmod(total_seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    milliseconds = td.microseconds // 1000
    return f"{hours:02d}:{minutes:02d}:{seconds:02d},{milliseconds:03d}"

def cut_video_audio_subs(video_file, srt_file, out_dir, segment_callback=None, naming_mode="index", preset="veryfast", crf="18"):
    ensure_dir(out_dir)
    subs = pysrt.open(srt_file, encoding="utf-8-sig")
    ext = os.path.splitext(video_file)[1].lower()
    is_audio = ext in [".mp3", ".wav", ".flac", ".aac", ".m4a", ".ogg"]
    # 打开媒体文件
    if is_audio:
        media = AudioFileClip(video_file)
    else:
        media = VideoFileClip(video_file)
    for i, sub in enumerate(subs, 1):
        # 命名逻辑
        if naming_mode == "subtitle":
            subtitle_text = sub.text.replace('\n', ' ').replace('\r', ' ')
            subtitle_text = ''.join(c for c in subtitle_text if c.isalnum() or c in (' ', '_', '-'))
            subtitle_text = subtitle_text.strip().replace(' ', '_')
            # 不再限制命名长度
            if not subtitle_text:
                subtitle_text = f"clip_{i}"
            name = f"{i:02d}.{subtitle_text}"
        else:
            name = f"{i:02d}"
        # 生成输出文件名
        if is_audio:
            out_path = os.path.join(out_dir, f"{name}.mp3")
        else:
            out_path = os.path.join(out_dir, f"{name}.mp4")
        print(f"✂️ 切割中 {os.path.basename(video_file)}: {i}/{len(subs)}")
        start = sub.start.ordinal / 1000
        end = sub.end.ordinal / 1000
        clip = media.subclip(start, end)
        # 直接用 out_path 保存
        if is_audio:
            clip.write_audiofile(out_path, logger=None)
        else:
            clip.write_videofile(
                out_path,
                codec="libx264",
                audio_codec="aac",
                bitrate="3000k",
                preset=preset,
                ffmpeg_params=["-crf", crf],
                logger=None
            )
            # 音频也可以按同名方式导出
            audio_path = os.path.join(out_dir, f"{name}.mp3")
            clip.audio.write_audiofile(audio_path, logger=None)
        # 字幕归零
        sub_rel = sub
        sub_rel.start.ordinal -= int(start * 1000)
        sub_rel.end.ordinal -= int(start * 1000)
        s_path = os.path.join(out_dir, f"{name}.srt")
        pysrt.SubRipFile([sub_rel]).save(s_path)
        if segment_callback:
            segment_callback()
    media.close()

def extract_leading_number(filename):
    """提取文件名前面的数字用于排序"""
    m = re.match(r"(\d+)", os.path.splitext(filename)[0])
    return int(m.group(1)) if m else 0

def merge_files(output_dir, ext, merged_file, progress_callback=None):
    files = [f for f in os.listdir(output_dir) if f.endswith(ext)]
    files.sort(key=extract_leading_number)
    if not files:
        return
    list_file = os.path.join(output_dir, f"list_{ext}.txt")
    with open(list_file, "w", encoding="utf-8") as f:
        for file in files:
            f.write(f"file '{os.path.abspath(os.path.join(output_dir, file))}'\n")
    cmd = [
        "ffmpeg", "-f", "concat", "-safe", "0", "-i", list_file,
        "-c", "copy", merged_file, "-y"
    ]
    subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=subprocess.CREATE_NO_WINDOW)
    os.remove(list_file)
    if progress_callback:
        progress_callback()

def merge_subtitles(output_dir, files, merged_srt, gap=0.1, duration_map=None, progress_callback=None):
    files.sort(key=extract_leading_number)
    merged_subs = []
    current_time = timedelta(seconds=0)
    for i, file in enumerate(files):
        srt_file = os.path.join(output_dir, f"{os.path.splitext(file)[0]}.srt")
        if not os.path.exists(srt_file):
            continue
        subs = pysrt.open(srt_file)
        for sub in subs:
            sub_start = timedelta(hours=sub.start.hours, minutes=sub.start.minutes, seconds=sub.start.seconds, milliseconds=sub.start.milliseconds)
            sub_end = timedelta(hours=sub.end.hours, minutes=sub.end.minutes, seconds=sub.end.seconds, milliseconds=sub.end.milliseconds)
            new_start = current_time + sub_start
            new_end = current_time + sub_end
            if merged_subs:
                prev_end = merged_subs[-1]['end']
                if new_start < prev_end + timedelta(seconds=gap):
                    new_start = prev_end + timedelta(seconds=gap)
                    if new_end < new_start:
                        new_end = new_start + timedelta(milliseconds=500)
            merged_subs.append({
                'index': len(merged_subs) + 1,
                'start': new_start,
                'end': new_end,
                'text': sub.text
            })
        # 用真实时长推进时间轴
        if duration_map:
            current_time += timedelta(seconds=duration_map[file])
        else:
            v_path = os.path.join(output_dir, file)
            current_time += timedelta(seconds=get_video_duration(v_path))
    with open(merged_srt, "w", encoding="utf-8") as f:
        for sub in merged_subs:
            f.write(f"{sub['index']}\n")
            f.write(f"{format_timedelta(sub['start'])} --> {format_timedelta(sub['end'])}\n")
            f.write(f"{sub['text']}\n\n")
    if progress_callback:
        progress_callback()

# 新增函数：单独合并视频、音频和字幕
# 修改 standalone_merge 函数以支持更多格式
def standalone_merge(input_folder, output_folder, progress_callback=None, log_callback=None):
    """单独合并已分割的视频、音频和字幕文件"""
    if not os.path.isdir(input_folder):
        if log_callback:
            log_callback("错误：输入文件夹不存在！")
        return
    
    # 支持的视频和音频格式
    video_exts = ['.mp4', '.mkv', '.avi', '.mov', '.flv', '.wmv', '.m4v', '.mpeg', '.mpg', '.ts', '.mts', '.m2ts']
    audio_exts = ['.mp3', '.wav', '.flac', '.aac', '.m4a', '.ogg', '.wma']
    
    # 获取所有分段文件
    video_files = [f for f in os.listdir(input_folder) if os.path.splitext(f)[1].lower() in video_exts]
    audio_files = [f for f in os.listdir(input_folder) if os.path.splitext(f)[1].lower() in audio_exts]
    srt_files = [f for f in os.listdir(input_folder) if f.endswith('.srt')]
    
    if not video_files and not audio_files:
        if log_callback:
            log_callback("错误：未找到任何视频或音频文件！")
        return
    
    # 排序文件
    video_files.sort(key=extract_leading_number)
    audio_files.sort(key=extract_leading_number)
    srt_files.sort(key=extract_leading_number)
    
    # 确定输出目录
    ensure_dir(output_folder)
    
    # 使用输出文件夹的名称作为基础名称
    base_name = os.path.basename(output_folder)
    if not base_name:
        base_name = "merged"
    
    # 计算总步骤数
    total_steps = (1 if video_files else 0) + (1 if audio_files else 0) + (1 if srt_files else 0)
    current_step = 0
    
    def update_progress():
        nonlocal current_step
        current_step += 1
        if progress_callback:
            progress_callback(int(current_step / total_steps * 100))
    
    # 合并视频（如果有）
    if video_files:
        # 获取主要视频格式（选择数量最多的格式）
        ext_counts = {}
        for file in video_files:
            ext = os.path.splitext(file)[1].lower()
            ext_counts[ext] = ext_counts.get(ext, 0) + 1
        
        main_video_ext = max(ext_counts, key=ext_counts.get)
        video_files_to_merge = [f for f in video_files if os.path.splitext(f)[1].lower() == main_video_ext]
        
        # 排序
        video_files_to_merge.sort(key=extract_leading_number)
        
        # 合并视频
        merged_video = os.path.join(output_folder, f"{base_name}{main_video_ext}")
        merge_files(input_folder, main_video_ext, merged_video, update_progress)
        if log_callback:
            log_callback(f"视频已合并到：{merged_video}")
    
    # 合并音频（如果有）
    if audio_files:
        # 获取主要音频格式（选择数量最多的格式）
        ext_counts = {}
        for file in audio_files:
            ext = os.path.splitext(file)[1].lower()
            ext_counts[ext] = ext_counts.get(ext, 0) + 1
        
        main_audio_ext = max(ext_counts, key=ext_counts.get)
        audio_files_to_merge = [f for f in audio_files if os.path.splitext(f)[1].lower() == main_audio_ext]
        
        # 排序
        audio_files_to_merge.sort(key=extract_leading_number)
        
        # 合并音频
        merged_audio = os.path.join(output_folder, f"{base_name}{main_audio_ext}")
        merge_files(input_folder, main_audio_ext, merged_audio, update_progress)
        if log_callback:
            log_callback(f"音频已合并到：{merged_audio}")
    
    # 合并字幕（如果有）
    if srt_files:
        # 创建时长映射
        duration_map = {}
        
        # 优先使用视频文件计算时长，如果没有视频则使用音频文件
        files_to_use = video_files if video_files else audio_files
        
        for file in files_to_use:
            file_path = os.path.join(input_folder, file)
            duration_map[file] = get_video_duration(file_path)
        
        merged_srt = os.path.join(output_folder, f"{base_name}.srt")
        merge_subtitles(input_folder, files_to_use, merged_srt, gap=0.2, duration_map=duration_map, progress_callback=update_progress)
        if log_callback:
            log_callback(f"字幕已合并到：{merged_srt}")
    
    if log_callback:
        log_callback("合并完成！")

# 修改 merge_files 函数以支持更多格式
def merge_files(output_dir, ext, merged_file, progress_callback=None):
    files = [f for f in os.listdir(output_dir) if f.endswith(ext)]
    files.sort(key=extract_leading_number)
    if not files:
        return
    list_file = os.path.join(output_dir, f"list_{ext.replace('.', '_')}.txt")
    with open(list_file, "w", encoding="utf-8") as f:
        for file in files:
            f.write(f"file '{os.path.abspath(os.path.join(output_dir, file))}'\n")
    
    # 根据文件扩展名确定输出格式和编码
    if ext in ['.mp4', '.mkv', '.avi', '.mov', '.flv', '.wmv', '.m4v', '.mpeg', '.mpg', '.ts', '.mts', '.m2ts']:
        # 视频文件
        cmd = [
            "ffmpeg", "-f", "concat", "-safe", "0", "-i", list_file,
            "-c", "copy", merged_file, "-y"
        ]
    else:
        # 音频文件
        cmd = [
            "ffmpeg", "-f", "concat", "-safe", "0", "-i", list_file,
            "-c", "copy", merged_file, "-y"
        ]
    
    subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=subprocess.CREATE_NO_WINDOW)
    os.remove(list_file)
    if progress_callback:
        progress_callback()

# 进度条变量
root = tk.Tk()
progress_var = tk.DoubleVar()
progress_percent_var = tk.StringVar(value="0%")

# 新增编码参数变量
preset_var = tk.StringVar(value="veryfast")
crf_var = tk.StringVar(value="24")

def batch_process(input_folder, output_root="output_root", progress_callback=None, log_callback=None, naming_mode="index", preset="veryfast", crf="18"):
    start_time = time.time()
    video_exts = [".mp4", ".mkv", ".avi", ".mp3", ".wav", ".flac", ".aac", ".m4a", ".ogg"]
    subtitle_ext = ".srt"
    ensure_dir(output_root)
    video_files = []
    for ext in video_exts:
        video_files.extend(glob(os.path.join(input_folder, f"*{ext}")))
        video_files = [f for f in video_files if "TEMP_MPY_wvf_snd" not in os.path.basename(f)]
    
    # 统计总分段数
    total_segments = 0
    for video_file in video_files:
        base_name = os.path.splitext(os.path.basename(video_file))[0]
        srt_file = os.path.join(input_folder, f"{base_name}{subtitle_ext}")
        if os.path.exists(srt_file):
            subs = pysrt.open(srt_file, encoding="utf-8-sig")
            total_segments += len(subs)
    
    if total_segments == 0:
        if log_callback:
            log_callback("未找到任何字幕分段，无法处理。")
        return
    
    current_segment = 0
    for video_file in video_files:
        base_name = os.path.splitext(os.path.basename(video_file))[0]
        chunk_dir = os.path.join(output_root, base_name, base_name)
        ensure_dir(chunk_dir)
        out_dir = os.path.join(output_root, base_name)
        ensure_dir(out_dir)
        srt_file = os.path.join(input_folder, f"{base_name}{subtitle_ext}")
        
        if not os.path.exists(srt_file):
            warn_msg = f"[WARNING] 未找到字幕: {srt_file}"
            print(warn_msg)
            if log_callback:
                log_callback(warn_msg)
            continue
        
        # 处理每个分段时回调进度
        def segment_callback():
            nonlocal current_segment
            current_segment += 1
            if progress_callback:
                progress_callback(current_segment, total_segments)
        
        cut_video_audio_subs(video_file, srt_file, chunk_dir, segment_callback, naming_mode, preset, crf)
        
        ext = os.path.splitext(video_file)[1].lower()
        is_audio = ext in [".mp3", ".wav", ".flac", ".aac", ".m4a", ".ogg"]
        
        if is_audio:
            audio_files = [f for f in os.listdir(chunk_dir) if f.endswith(".mp3")]
            audio_files.sort(key=extract_leading_number)
            merged_audio = os.path.join(out_dir, f"{base_name}.mp3")
            merge_files(chunk_dir, ".mp3", merged_audio)
            duration_map = {f: get_video_duration(os.path.join(chunk_dir, f)) for f in audio_files}
            merged_srt_audio = os.path.join(out_dir, f"{base_name}.srt")
            merge_subtitles(chunk_dir, audio_files, merged_srt_audio, gap=0.2, duration_map=duration_map)
        else:
            clip_files = [f for f in os.listdir(chunk_dir) if f.endswith(".mp4")]
            clip_files.sort(key=extract_leading_number)
            audio_files = [f for f in os.listdir(chunk_dir) if f.endswith(".mp3")]
            audio_files.sort(key=extract_leading_number)
            merged_video = os.path.join(out_dir, f"{base_name}.mp4")
            merge_files(chunk_dir, ".mp4", merged_video)
            merged_audio = os.path.join(out_dir, f"{base_name}.mp3")
            merge_files(chunk_dir, ".mp3", merged_audio)
            duration_map = {f: get_video_duration(os.path.join(chunk_dir, f)) for f in clip_files}
            merged_srt_video = os.path.join(out_dir, f"{base_name}.srt")
            merge_subtitles(chunk_dir, clip_files, merged_srt_video, gap=0.2, duration_map=duration_map)
    
    elapsed = time.time() - start_time
    finish_msg = f"\n🏁 总运行时间: {int(elapsed // 60):02d} 分 {int(elapsed % 60):02d} 秒"
    print(finish_msg)
    if log_callback:
        log_callback(finish_msg)

# 设置 ffmpeg 路径
def get_path(relative_path):
    try:
        base_path = sys._MEIPASS
    except AttributeError:
        base_path = os.path.abspath(".")
    return os.path.normpath(os.path.join(base_path, relative_path))

ffmpeg_path = get_path("ffmpeg.exe")
if os.path.exists(ffmpeg_path):
    change_settings({"FFMPEG_BINARY": ffmpeg_path})
else:
    # 检查环境变量
    if shutil.which("ffmpeg"):
        change_settings({"FFMPEG_BINARY": "ffmpeg"})
    else:
        messagebox.showerror("错误", "未找到 ffmpeg.exe，请将其与本程序放在同一目录，或将其路径加入环境变量！")
        sys.exit(1)

# 设置窗口图标
icon_path = get_path("cut.ico")
root.iconbitmap(icon_path)

def select_input():
    path = filedialog.askdirectory(title="选择输入文件夹")
    if path:
        input_var.set(path)

def select_output():
    path = filedialog.askdirectory(title="选择输出文件夹")
    if path:
        output_var.set(path)

def select_merge_input():
    path = filedialog.askdirectory(title="选择包含分段文件的文件夹")
    if path:
        merge_input_var.set(path)

def select_merge_output():
    path = filedialog.askdirectory(title="选择输出文件夹")
    if path:
        merge_output_var.set(path)

def append_log(msg):
    log_text.config(state='normal')
    log_text.insert(tk.END, msg + '\n')
    log_text.see(tk.END)
    log_text.config(state='disabled')
    root.update_idletasks()

def update_progress(idx, total):
    percent = int(idx / total * 100) if total else 0
    progress_var.set(percent)
    progress_percent_var.set(f"{percent}%")
    root.update_idletasks()

def update_merge_progress(percent):
    progress_var.set(percent)
    progress_percent_var.set(f"{percent}%")
    root.update_idletasks()

# 存储按钮引用
start_button = None
merge_button = None

def start_process():
    in_dir = input_var.get()
    out_dir = output_var.get()
    if not os.path.isdir(in_dir):
        messagebox.showerror("错误", "请输入有效的输入文件夹路径！")
        return
    if not os.path.isdir(out_dir):
        messagebox.showerror("错误", "请输入有效的输出文件夹路径！")
        return

    # 禁用按钮
    start_button.config(state='disabled')
    merge_button.config(state='disabled')
    
    mode = naming_mode.get()
    preset = preset_var.get()
    crf = crf_var.get()
    
    def run_batch():
        import sys
        class PrintLogger:
            def write(self, msg):
                if msg.strip():
                    append_log(msg.strip())
            def flush(self): pass
        old_stdout = sys.stdout
        sys.stdout = PrintLogger()
        try:
            batch_process(
                in_dir, out_dir,
                progress_callback=update_progress,
                log_callback=append_log,
                naming_mode=mode,
                preset=preset,
                crf=crf
            )
            append_log("处理完成！")
            messagebox.showinfo("提示", f"处理完成！\n输入：{in_dir}\n输出：{out_dir}")
        finally:
            sys.stdout = old_stdout
            progress_var.set(100)
            progress_percent_var.set("100%")
            # 重新启用按钮
            start_button.config(state='normal')
            merge_button.config(state='normal')

    progress_var.set(0)
    progress_percent_var.set("0%")
    log_text.config(state='normal')
    log_text.delete(1.0, tk.END)
    log_text.config(state='disabled')
    threading.Thread(target=run_batch, daemon=True).start()

# 存储合并窗口中的按钮引用
merge_start_button = None
merge_open_output_button = None

def start_merge():
    input_dir = merge_input_var.get()
    output_dir = merge_output_var.get()
    
    if not os.path.isdir(input_dir):
        messagebox.showerror("错误", "请输入有效的输入文件夹路径！")
        return
    
    if not output_dir:
        messagebox.showerror("错误", "请输入有效的输出文件夹路径！")
        return
    
    # 禁用开始合并按钮
    if merge_start_button:
        merge_start_button.config(state='disabled')
    
    # 重置进度条
    progress_var.set(0)
    progress_percent_var.set("0%")
    
    # 添加虚拟进度提示
    def show_virtual_progress():
        for i in range(1, 6):
            progress_var.set(i * 5)
            progress_percent_var.set(f"{i * 5}%")
            time.sleep(0.1)
            root.update_idletasks()
    
    # 运行虚拟进度提示
    threading.Thread(target=show_virtual_progress, daemon=True).start()
    
    def run_merge():
        import sys
        class PrintLogger:
            def write(self, msg):
                if msg.strip():
                    append_log(msg.strip())
            def flush(self): pass
        old_stdout = sys.stdout
        sys.stdout = PrintLogger()
        try:
            standalone_merge(
                input_dir, output_dir,
                progress_callback=update_merge_progress,
                log_callback=append_log
            )
            append_log("合并完成！")
            messagebox.showinfo("提示", f"合并完成！\n输入：{input_dir}\n输出：{output_dir}")
        finally:
            sys.stdout = old_stdout
            progress_var.set(100)
            progress_percent_var.set("100%")
            # 启用开始合并按钮
            if merge_start_button:
                merge_start_button.config(state='normal')

    log_text.config(state='normal')
    log_text.delete(1.0, tk.END)
    log_text.config(state='disabled')
    threading.Thread(target=run_merge, daemon=True).start()

def open_merge_output():
    output_dir = merge_output_var.get()
    if os.path.isdir(output_dir):
        os.startfile(output_dir)
    else:
        messagebox.showerror("错误", "输出文件夹不存在！")

def open_output():
    out_dir = output_var.get()
    if os.path.isdir(out_dir):
        os.startfile(out_dir)
    else:
        messagebox.showerror("错误", "输出文件夹不存在！")

def show_help():
    help_text = (
        """
【按字幕切割音视频工具 使用说明】

一、功能简介
-本工具可根据字幕文件（.srt）自动将视频或音频文件按句切割，并支持批量处理、自动合并片段、导出分段字幕。适用于外语学习、素材整理、短视频制作等场景。

二、环境要求
- Windows 10/11 操作系统
- Python 3.7 及以上（如使用 EXE 版可无须安装 Python）
- 需将 ffmpeg.exe、ffprobe.exe、ffplay.exe 放在本程序同一目录，或将其路径加入系统环境变量

三、文件准备
-视频/音频文件：支持 mp4、mkv、mp3、wav、aac等格式。
-字幕文件：需为标准 SRT 格式，且文件名与视频/音频一致（如 video1.mp4 和 video1.srt）。

四、操作步骤
1. 输入文件夹路径
- 选择包含视频/音频及对应字幕（.srt）文件的文件夹。视频和字幕文件名需一致（如 video1.mp4 和 video1.srt）。
2. 输出文件夹路径
- 选择处理后文件的保存目录。
3. 分割片段命名方式
- 按序号：片段文件名为 "01.mp4"、"02.mp4" 等。
- 按序号+字幕内容：片段文件名为"01.字幕内容.mp4"，便于识别。
4. FFmpeg编码预设
- 影响处理速度和输出文件大小。推荐 veryfast 或  medium，如需更高画质可选 veryslow 或 slow（但更耗时）。
5. CRF质量参数
- 数值越小画质越高，文件越大。常用 18~28，推荐 24。
-输出MP4格式的视频，视频流采用x264编码，音频流采用AAC编码。 
先设定CRF值，来确定画质。数字越小画质越高，数字越大画质越差，一般在设定在16-24之间。
在其他参数不变的情况下，CRF值每减小6，输出的文件会变大一倍左右。接着设定Preset,来确定编码速度。
编码越快，生成的文件越大；编码越慢，文件越小，一般设 定在4-8之间。CRF模式下，该数值基本不影响画质，只和编码速度、文件大小有关。   
6. 开始处理
-点击"开始处理"按钮，软件将自动按字幕切割视频/音频/字幕片段，并合并输出。
7. 查看输出
- 处理完成后，可点击"打开输出文件夹"快速定位结果。输出结构示例：           
            输出目录结构如下： 
              output_root/ 
                  ├──input_video/ 
                  ├── input_video/      # 片段专用子文件夹 
                  │   ├── 01.mp4 
                  │   ├── 01.mp3 
                  │   ├── 01.srt 
                  │   └──... 
                  ├── input_video.mp4   # 合并后的视频
                  ├── input_video.mp3   # 合并后的音频
                  └── input_video.srt   # 合并后的字幕

五、片段合并功能
- 此功能允许您将已经分割好的视频、音频和字幕文件重新合并
- 选择包含分段文件的文件夹（通常是切割后生成的子文件夹）
- 指定输出文件夹
- 点击"开始合并"按钮执行合并操作
- 合并后的文件将使用输出文件夹的名称作为基础名称

六、常见问题
- 找不到 ffmpeg
- 请确保 ffmpeg.exe、ffprobe.exe、ffplay.exe 与本程序在同一目录，或已加入环境变量。
- 进度条异常或程序崩溃
- 检查内存是否充足，建议关闭其他大型程序。尽量选择 veryfast 或更快的编码预设。如遇"MemoryError"或"Broken pipe"报错，建议重启电脑或降低分辨率。
- 字幕或视频未被切割
- 检查字幕文件名与视频/音频文件名是否一致，确认字幕文件为标准 SRT 格式。
- 输出文件名乱码或过长
- 建议输入输出路径及字幕内容避免特殊符号。按序号命名可避免文件名过长。

七、注意事项
- 处理过程中请勿关闭窗口或移动/删除源文件。
- 文件夹和文件路径请勿包含特殊符号（如 ! @ # $ % ^ & * 空格 等），建议只用中英文、数字、下划线。
- 如遇问题可点击"使用帮助"按钮或联系开发者。
- 开源网址：https://github.com/fengye398-cpu/cut_video_audio_subs
"""     
    )
    help_win = tk.Toplevel(root)
    help_win.title("使用帮助")
    help_win.geometry("600x500")
    help_win.iconbitmap(icon_path)
    
    # 设置窗口为普通窗口，允许调整大小
    help_win.resizable(True, True)
    help_win.minsize(500, 400)
    
    # 创建主框架
    main_frame = tk.Frame(help_win)
    main_frame.pack(fill="both", expand=True, padx=10, pady=10)
    
    # 创建文本框架和滚动条
    text_frame = tk.Frame(main_frame)
    text_frame.pack(fill="both", expand=True)
    
    # 创建滚动条
    scrollbar = tk.Scrollbar(text_frame)
    scrollbar.pack(side="right", fill="y")
    
    text = tk.Text(text_frame, wrap="word", font=("微软雅黑", 11), yscrollcommand=scrollbar.set)
    text.pack(side="left", fill="both", expand=True)
    scrollbar.config(command=text.yview)
    
    text.insert(tk.END, help_text)
    text.config(state="disabled")
    
    # 创建按钮框架
    button_frame = tk.Frame(main_frame)
    button_frame.pack(fill="x", pady=10)
    
    # 添加关闭按钮
    close_button = tk.Button(button_frame, text="关闭", command=help_win.destroy, width=10)
    close_button.pack()
    
    # 窗口关闭时释放grab
    def on_close():
        help_win.grab_release()
        help_win.destroy()
    
    help_win.protocol("WM_DELETE_WINDOW", on_close)

# 创建合并相关的变量
merge_input_var = tk.StringVar()
merge_output_var = tk.StringVar()

# 创建合并窗口
# 在文件开头添加全局变量
merge_window_open = False

# 修改 open_merge_window 函数
def open_merge_window():
    global merge_window_open, merge_start_button, merge_open_output_button
    
    if merge_window_open:
        return  # 如果窗口已经打开，则不执行任何操作
    
    merge_window_open = True
    
    merge_win = tk.Toplevel(root)
    merge_win.title("单独合并视频音频字幕")
    merge_win.geometry("650x150")
    merge_win.iconbitmap(icon_path)
    
    # 设置窗口为模态
    merge_win.transient(root)
    merge_win.grab_set()
    merge_win.focus_set()
    
    tk.Label(merge_win, text="输入文件夹路径：").grid(row=0, column=0, sticky="e", padx=5, pady=5)
    tk.Entry(merge_win, textvariable=merge_input_var, width=50).grid(row=0, column=1, padx=5)
    tk.Button(merge_win, text="选择", command=select_merge_input).grid(row=0, column=2, padx=5)
    
    tk.Label(merge_win, text="输出文件夹路径：").grid(row=1, column=0, sticky="e", padx=5, pady=5)
    tk.Entry(merge_win, textvariable=merge_output_var, width=50).grid(row=1, column=1, padx=5)
    tk.Button(merge_win, text="选择", command=select_merge_output).grid(row=1, column=2, padx=5)
    
    # 创建按钮框架
    button_frame = tk.Frame(merge_win)
    button_frame.grid(row=2, column=1, pady=10, sticky="ew")
    
    # 开始合并按钮
    merge_start_button = tk.Button(button_frame, text="开始合并", command=start_merge, width=12)
    merge_start_button.pack(side="left", padx=5)
    
    # 打开输出文件夹按钮
    merge_open_output_button = tk.Button(button_frame, text="打开输出文件夹", command=open_merge_output, width=15)
    merge_open_output_button.pack(side="left", padx=5)
    
    # 添加窗口关闭时的处理
    def on_close():
        global merge_window_open
        merge_window_open = False
        merge_win.grab_release()
        merge_win.destroy()
    
    merge_win.protocol("WM_DELETE_WINDOW", on_close)

root.title("按字幕切割音视频工具V0.4")
icon_path = get_path("cut.ico")
root.iconbitmap(icon_path)
root.geometry("700x600")

input_var = tk.StringVar()
output_var = tk.StringVar()
naming_mode = tk.StringVar(value="index")  # 默认按序号

tk.Label(root, text="分割片段命名方式：").grid(row=5, column=0, sticky="e", padx=5, pady=5)
tk.Radiobutton(root, text="按序号", variable=naming_mode, value="index").grid(row=5, column=1, sticky="w")
tk.Radiobutton(root, text="按序号+字幕内容", variable=naming_mode, value="subtitle").grid(row=5, column=2, sticky="w")

# FFmpeg编码预设
tk.Label(root, text="FFmpeg编码预设：").grid(row=6, column=0, sticky="e", padx=5, pady=5)
preset_choices = ["ultrafast", "superfast", "veryfast", "faster", "fast", "medium", "slow", "slower", "veryslow"]
preset_combo = ttk.Combobox(root, textvariable=preset_var, values=preset_choices, state="readonly", width=8)
preset_combo.grid(row=6, column=1, sticky="w", padx=2)
preset_combo.current(preset_choices.index(preset_var.get()))

# CRF质量参数
tk.Label(root, text="CRF质量参数：").grid(row=6, column=2, sticky="e", padx=5, pady=5)
crf_choices = [str(i) for i in range(16, 32)]
crf_combo = ttk.Combobox(root, textvariable=crf_var, values=crf_choices, state="readonly", width=8)
crf_combo.grid(row=6, column=3, sticky="w", padx=2)
crf_combo.current(crf_choices.index(crf_var.get()))

tk.Label(root, text="输入文件夹路径：").grid(row=0, column=0, sticky="e", padx=5, pady=5)
tk.Entry(root, textvariable=input_var, width=50).grid(row=0, column=1, padx=5)
tk.Button(root, text="选择", command=select_input).grid(row=0, column=2, padx=5)

tk.Label(root, text="输出文件夹路径：").grid(row=1, column=0, sticky="e", padx=5, pady=5)
tk.Entry(root, textvariable=output_var, width=50).grid(row=1, column=1, padx=5)
tk.Button(root, text="选择", command=select_output).grid(row=1, column=2, padx=5)

# 创建按钮并存储引用
start_button = tk.Button(root, text="开始处理", command=start_process, width=12)
start_button.grid(row=2, column=0, pady=10)

tk.Button(root, text="打开输出文件夹", command=open_output, width=16).grid(row=2, column=1)

merge_button = tk.Button(root, text="片段合并", command=open_merge_window, width=8)
merge_button.grid(row=2, column=2)

tk.Button(root, text="使用帮助", command=show_help, width=10).grid(row=2, column=3, padx=5)
tk.Button(root, text="退出", command=root.quit, width=8).grid(row=2, column=4, padx=5)

# 进度条
tk.Label(root, text="进度：").grid(row=3, column=0, sticky="e", padx=5, pady=5)
progressbar = ttk.Progressbar(root, variable=progress_var, maximum=100)
progressbar.grid(row=3, column=1, sticky="ew", padx=5, pady=5)  # 关键：sticky="ew"
tk.Label(root, textvariable=progress_percent_var, width=5).grid(row=3, column=2, sticky="ew")  # 关键：sticky="ew"

# 让第1列（即进度条所在列）自动拉伸
root.grid_columnconfigure(1, weight=1)

tk.Label(root, text="日志：").grid(row=4, column=0, sticky="nw", padx=5, pady=5)

# 创建日志框架和滚动条
log_frame = tk.Frame(root)
log_frame.grid(row=4, column=1, columnspan=4, padx=5, pady=5, sticky="nsew")

# 创建垂直滚动条
log_scrollbar = tk.Scrollbar(log_frame)
log_scrollbar.pack(side="right", fill="y")

log_text = tk.Text(log_frame, width=80, height=20, state='disabled', yscrollcommand=log_scrollbar.set)
log_text.pack(side="left", fill="both", expand=True)

log_scrollbar.config(command=log_text.yview)

root.grid_rowconfigure(4, weight=1)
root.grid_columnconfigure(1, weight=1)

root.mainloop()