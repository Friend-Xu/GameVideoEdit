import tkinter
from time import sleep
from threading import Thread
from tkinter.filedialog import askopenfilename
import pyaudio
from PIL import Image, ImageTk
from moviepy.video.io import VideoFileClip

root = tkinter.Tk()
root.title('视频播放器')
root.geometry('860x640+200+100')
isPlaying = False

# 用来显示视频画面的Label组件，自带双缓冲，不闪烁
lbVideo = tkinter.Label(root, bg='white')
lbVideo.pack(fill=tkinter.BOTH, expand=tkinter.YES)


def play_video(video):
    vw = video.w
    vh = video.h
    # 逐帧播放画面
    for frame in video.iter_frames(fps=video.fps / 2.5):
        if not isPlaying:
            break
        w = root.winfo_width()
        h = root.winfo_height()
        # 保持原视频的纵横比
        ratio = min(w / vw, h / vh)
        size = (int(vw * ratio), int(vh * ratio))
        frame = Image.fromarray(frame).resize(size)
        frame = ImageTk.PhotoImage(frame)
        lbVideo['image'] = frame
        lbVideo.image = frame
        lbVideo.update()


def play_audio(audio):
    p = pyaudio.PyAudio()
    # 创建输出流
    stream = p.open(format=pyaudio.paFloat32,
                    channels=2,
                    rate=44100,
                    output=True)
    # 逐帧播放音频
    for chunk in audio.iter_frames():
        if not isPlaying:
            break
        stream.write(chunk.astype('float32').tostring())
    p.terminate()


# 创建主菜单
mainMenu = tkinter.Menu(root)

# 创建子菜单
# tearoff=1时菜单顶部会有个虚线
# 单击虚线之后可以使得菜单从窗口中分离处理单独显示

subMenu = tkinter.Menu(tearoff=0)


def open_video():
    global isPlaying
    isPlaying = False
    fn = askopenfilename(title='打开视频文件',
                         filetypes=[('视频', '*.mp4 *.avi')])
    if fn:
        root.title(f'视频播放器-正在播放"{fn}"')
        video = VideoFileClip(fn)
        audio = video.audio
        isPlaying = True
        # 播放视频的线程
        t1 = Thread(target=play_video, args=(video,))
        t1.daemon = True
        t1.start()
        # 播放音频的线程
        t2 = Thread(target=play_audio, args=(audio,))
        t2.daemon = True
        t2.start()


# 添加菜单项，设置命令
subMenu.add_command(label='打开视频文件',
                    command=open_video)
# 把子菜单挂到主菜单上
mainMenu.add_cascade(label='文件',
                     menu=subMenu)
# 把主菜单放置到窗口上
root['menu'] = mainMenu


# 确保子线程关闭，
def exiting():
    global isPlaying
    isPlaying = False
    sleep(0.05)
    root.destroy()


root.protocol('WM_DELETE_WINDOW', exiting)
root.mainloop()
