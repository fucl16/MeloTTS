# MeloTTS 实时语音合成双流服务

该文档介绍基于 WebSocket 的 MeloTTS 双流服务实现方式。服务遵循定制二进制帧协议，可实现文本流输入与音频流输出的实时交互。

## 启动服务

```bash
python -m melo.stream_ws --host 0.0.0.0 --port 8010
```

服务端默认使用单模型并限制同时处理的合成任务不超过 100 个，满足百级并发需求。

模型在首次请求时按需加载，所有连接断开后自动卸载并清理显存，避免长期占用资源。

## 交互流程

协议与消息类型与示例中保持一致：

1. `START_CONNECTION` 建立连接。
2. `START_SESSION` 指定角色音色并启动会话。
3. 多次 `TASK_REQUEST` 发送文本片段，服务端以 `AUDIO_ONLY` `TTS_RESPONSE` 帧返回 PCM 音频。
4. `FINISH_SESSION` 结束会话，`FINISH_CONNECTION` 关闭连接。

## 多实例部署

服务无状态，可水平扩展。以下示例展示了通过 Docker Compose 启动 4 个实例以承担约 100 并发连接：

```yaml
version: '3'
services:
  tts:
    build: .
    command: python -m melo.stream_ws --host 0.0.0.0 --port 8010
    ports:
      - "8010:8010"
    deploy:
      replicas: 4
```

在 Kubernetes 中部署：

```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: melotts
spec:
  replicas: 4
  selector:
    matchLabels:
      app: melotts
  template:
    metadata:
      labels:
        app: melotts
    spec:
      containers:
        - name: melotts
          image: <your-image>
          ports:
            - containerPort: 8010
```

通过负载均衡将请求分发到不同实例，即可扩展到更高并发。
