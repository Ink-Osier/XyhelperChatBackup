import os
import json
from apscheduler.schedulers.background import BackgroundScheduler
import requests
from datetime import datetime
import mysql.connector
from mysql.connector import Error
from contextlib import closing
import logging
from logging.handlers import TimedRotatingFileHandler
import time
import pytz

# 读取环境变量
USER_TOKENS = json.loads(os.getenv("USER_TOKENS", "[]"))  # 假设环境变量中存储的是JSON编码的列表
DB_HOST = os.getenv("DB_HOST")
DB_PORT = os.getenv("DB_PORT", "3306")  # MySQL的默认端口是3306
DB_NAME = os.getenv("DB_NAME")
DB_USER = os.getenv("DB_USER")
DB_PASSWORD = os.getenv("DB_PASSWORD")
BASE_URL = os.getenv("BASE_URL")
USER_AGENT = os.getenv("USER_AGENT")

# 定义数据库连接
def get_db_connection():
    return mysql.connector.connect(
        host=DB_HOST,
        port=DB_PORT,
        database=DB_NAME,
        user=DB_USER,
        password=DB_PASSWORD
    )


# 设置日志级别
log_level_dict = {
    'DEBUG': logging.DEBUG,
    'INFO': logging.INFO,
    'WARNING': logging.WARNING,
    'ERROR': logging.ERROR,
    'CRITICAL': logging.CRITICAL
}

log_formatter = logging.Formatter('%(asctime)s [%(levelname)s] - %(message)s')

logger = logging.getLogger()
logger.setLevel(log_level_dict.get("DEBUG", logging.INFO))

log_filename = f'./logs/access.log'
file_handler = TimedRotatingFileHandler(log_filename, when="midnight", interval=1, backupCount=30)
file_handler.setFormatter(log_formatter)
logger.addHandler(file_handler)

# 添加标准输出流处理器（控制台输出）
stream_handler = logging.StreamHandler()
stream_handler.setFormatter(log_formatter)
logger.addHandler(stream_handler)

# 定义备份方法
def backup_chat_for_token(usertoken, first_time=False):
    current_time = datetime.now(pytz.utc)  # 假设数据库存储的时间为UTC
    # 连接数据库
    with closing(get_db_connection()) as conn:
        with conn.cursor(buffered=True) as cursor:
            # 获取需要备份的conversation id及email
            cursor.execute("""
                SELECT convid, email, updateTime 
                FROM chatgpt_conversations 
                WHERE usertoken = %s AND (deleted_at IS NULL OR deleted_at > %s)
            """, (usertoken, current_time))
            conversations = cursor.fetchall()
            
            valid_conversations = []
            for convid, email, update_time in conversations:
                # 根据email获取对应的status
                cursor.execute("SELECT status FROM chatgpt_session WHERE email = %s", (email,))
                status = cursor.fetchone()[0] if cursor.rowcount else None
                
                if status == 1:  # 如果status为1，则保留
                    # 检查updateTime是否在一个小时以内，或为首次备份（无updateTime）

                    # 为datetime对象添加UTC时区信息，使其成为offset-aware
                    update_time_aware = update_time.replace(tzinfo=pytz.utc)
                    if first_time or (current_time - update_time_aware).total_seconds() < 3600:
                        if first_time:
                            logger.info(f"First time backup for conversation: {convid} for user: {usertoken}")
                        else:
                            logger.info(f"Conversation: {convid} has been updated for user: {usertoken}")
                        valid_conversations.append((convid, email))
                    else:
                        logger.info(f"Conversation: {convid} has not been updated for user: {usertoken}")
    
    # 对每个有效的conversation id发起请求
    for convid, email in valid_conversations:
        logger.info(f"Start to backup conversation: {convid} for user: {usertoken}")
        response = requests.get(
            f"{BASE_URL}/backend-api/conversation/{convid}",
            headers={
                "Authorization": f"Bearer {usertoken}",
                "User-Agent": USER_AGENT
            }
        )
        if response.status_code == 200:
            data = response.json()
            save_conversation_to_markdown(data, convid, email, usertoken)
        else:
            logger.error(f"Failed to backup conversation: {convid} for user: {usertoken}. Status code: {response.status_code}, Response: {response.text}")
        
        time.sleep(1)  # 为了防止请求过于频繁，这里暂停1秒


# 将对话保存为Markdown文件
def save_conversation_to_markdown(data, convid, email, usertoken):
    try:
        title = data["title"]
        create_time = datetime.fromtimestamp(data["create_time"]).strftime('%Y-%m-%d %H:%M:%S')
        update_time = datetime.fromtimestamp(data["update_time"]).strftime('%Y-%m-%d %H:%M:%S')
        
        directory = os.path.join("conversations_history", usertoken, email)
        os.makedirs(directory, exist_ok=True)
        
        with open(os.path.join(directory, f"{title.replace('/', '_')}_{convid}.md"), "w", encoding="utf-8") as file:
            file.write(f"# {title}\n")
            file.write(f"> 对话创建时间: {create_time}\n")
            file.write(f"> 对话最近更新时间: {update_time}\n\n")
            
            for _, item in data["mapping"].items():
                if item["message"] and 'parts' in item["message"]["content"]:
                    # 确保parts列表不为空，并且至少有一个非空字符串
                    parts = item["message"]["content"]["parts"]
                    if parts != [] and any(part.strip() for part in parts):
                        role = item["message"]["author"]["role"]
                        if role == "user":
                            role_label = "You"
                        elif role == "assistant":
                            role_label = "ChatGPT"
                        elif role == "system":
                            role_label = "System"
                        else:
                            role_label = role  # 对于未知角色，你可以选择如何表示
                        content = "\n".join(parts)
                        file.write(f"#### {role_label}\n")
                        file.write(f"{content}\n")
                else:
                    # 处理没有'parts'的情况
                    # 例如：记录错误、跳过或写入一个默认消息
                    continue  # 这里简单地选择跳过这些消息
        
        logger.info(f"Conversation: {convid} has been saved to {directory}")
    except Exception as e:
        logger.error(f"Failed to save conversation: {convid} to file. Error: {e}")
        logger.info(f"Conversation data: {data}")


# 定义为每个token执行备份的方法
def backup_chats():
    backup_unit()

def backup_unit(is_first_time=False):
    for usertoken in USER_TOKENS:
        logger.info(f"Start to backup chats for user: {usertoken}")
        backup_chat_for_token(usertoken, is_first_time)

# 设置定时任务
scheduler = BackgroundScheduler()
scheduler.add_job(backup_chats, 'interval', hours=1)
scheduler.start()

# 注意：为了防止立即执行，实际部署时应该取消下一行的注释

import flask
app = flask.Flask(__name__)
if __name__ == '__main__':
    backup_unit(True)
    app.run()
