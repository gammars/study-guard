# StudyGuard Demo

基于项目书实现的树莓派双端学习陪伴 Agent demo。

## 已实现

- 学生端 `/student`
- 家长端 `/parent`
- 学习开始、结束、休息开始/结束、确认回座、环境读取、拍照、日报生成
- 后台在座监测：超声波 + YOLOv5n person detector 视觉检测融合
- MCP Server 工具注册与 OpenAI-compatible Agent 工具调用链
- 学生端语音输入、系统消息 TTS 播报
- 离座触发提醒，RGB 变红；学习中绿色，休息中蓝色
- 工具调用轨迹和学习日志
- 当前沙箱无法打开 GPIO 时会标记该传感器不可用，不再用 demo 距离冒充真实读数

## 运行

安装依赖：

```bash
pip install -r requirements.txt
```

复制环境变量模板并填写真实 key：

```bash
cp .env.example .env
```

启动服务：

```bash
/home/cjy/lab4/env/bin/python /home/cjy/studyguard/web/app.py
```

浏览器访问：

- 学生端：http://127.0.0.1:5050/student
- 家长端：http://127.0.0.1:5050/parent

## 硬件参数

- 摄像头：默认自动尝试 `cv2.VideoCapture(0..3)`，也可用 `STUDYGUARD_CAMERA_INDEX` 指定
- 视觉模型：`models/yolov5n.onnx`
- 超声波：`DistanceSensor(24, 23, max_distance=1, threshold_distance=0.40)`
- 离座阈值：超声波距离大于 `40cm` 判定为距离上离座
- DHT11：`adafruit_dht.DHT11(board.D3)`
- RGB LED：红 `5`，绿 `6`，蓝 `11`
- 按键：`26`

## 演示

1. 学生端点击“开始学习”。
2. 人离开座位，确保超声波距离大于 40cm，并且摄像头画面中 YOLOv5n 检测不到人。
3. 后台检测确认后，系统会发送离座提醒、暂停学习计时并把 LED 状态切为红色。
4. 回到座位后，点击“我回来了”或等待系统检测回座，状态恢复学习中。
5. 家长端输入“刚才有没有离座？”或“生成今天的学习报告”。
