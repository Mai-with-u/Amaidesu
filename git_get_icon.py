import os
import subprocess
import requests

# --- 配置 ---
# 头像保存的目录 (建议放在 .git 目录内，这样不会被 Git 跟踪)
AVATAR_DIR = ".git/avatars"
# --- 结束配置 ---

def get_contributors():
    """从 git log 获取贡献者列表 (名字和邮箱)"""
    try:
        # 使用'|'作为分隔符，方便处理
        log_output = subprocess.check_output(
            ['git', 'log', '--pretty=format:%an|%ae']
        ).decode('utf-8', errors='ignore')

        contributors = set(log_output.strip().split('\n'))
        return contributors
    except Exception as e:
        print(f"Error getting git log: {e}")
        return set()

def fetch_avatar(session, email, author_name):
    """使用 GitHub API 搜索用户邮箱并下载头像"""
    try:
        # 优先使用GitHub API搜索，因为它更准确
        api_url = f"https://api.github.com/search/users?q={email}+in:email"
        response = session.get(api_url)
        response.raise_for_status()
        data = response.json()

        avatar_url = None
        if data.get('items'):
            avatar_url = data['items'][0]['avatar_url']
            print(f"Found GitHub user for {author_name} ({email})")

        if avatar_url:
            # 下载头像
            avatar_response = session.get(avatar_url, stream=True)
            avatar_response.raise_for_status()

            # 以 "作者名.jpg" 的格式保存图片，Gource会识别这个文件名
            file_path = os.path.join(AVATAR_DIR, f"{author_name}.jpg")
            with open(file_path, 'wb') as f:
                for chunk in avatar_response.iter_content(1024):
                    f.write(chunk)
            print(f"  -> Saved avatar to {file_path}")
            return True

    except requests.exceptions.RequestException as e:
        print(f"API request failed for {email}: {e}")
    except Exception as e:
        print(f"An error occurred for {email}: {e}")

    print(f"Could not find avatar for {author_name} ({email})")
    return False

def main():
    """主函数"""
    if not os.path.exists(AVATAR_DIR):
        print(f"Creating avatar directory: {AVATAR_DIR}")
        os.makedirs(AVATAR_DIR)

    contributors = get_contributors()
    if not contributors:
        print("No contributors found or failed to read git log.")
        return

    print(f"Found {len(contributors)} unique contributors. Fetching avatars...")

    # 使用 Session 来复用连接，并设置 GitHub API 版本头
    with requests.Session() as session:
        session.headers.update({'Accept': 'application/vnd.github.v3+json'})
        for contributor in contributors:
            try:
                author_name, email = contributor.split('|')
                # 检查头像是否已存在
                if not os.path.exists(os.path.join(AVATAR_DIR, f"{author_name}.jpg")):
                     fetch_avatar(session, email, author_name)
                else:
                    print(f"Avatar for {author_name} already exists. Skipping.")
            except ValueError:
                print(f"Skipping malformed log entry: {contributor}")

    print("\nAvatar fetching complete!")
    print(f"You can now run Gource with: gource --user-image-dir {AVATAR_DIR}")

if __name__ == "__main__":
    main()