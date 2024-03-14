# 使用官方Python镜像作为基础镜像
FROM python:3.9-slim

# 设置工作目录为/app
WORKDIR /app

# 将当前目录下的所有文件复制到容器的/app目录下
COPY ./main.py /app/main.py

# 安装pip依赖
RUN pip install --no-cache-dir apscheduler mysql-connector-python requests flask

# 当容器启动时运行Python脚本
CMD ["python", "./main.py"]
