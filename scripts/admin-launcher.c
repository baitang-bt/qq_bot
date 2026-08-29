/* DEPRECATED: use scripts/desktop-app-launcher.sh (shell .app entry, no clang). */
#include <errno.h>
#include <libgen.h>
#include <limits.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

static const char *kProjectRoot =
    "/Users/max/Documents/Cursor/projects/qq-chat-bot";

static void alert(const char *message) {
    char cmd[1024];
    snprintf(
        cmd,
        sizeof(cmd),
        "osascript -e 'display alert \"QQ机器人管理\" message \"%s\"'",
        message
    );
    system(cmd);
}

int main(void) {
    char python[PATH_MAX];
    char logpath[PATH_MAX];
    char shell_cmd[PATH_MAX * 2];

    snprintf(python, sizeof(python), "%s/.venv/bin/python", kProjectRoot);
    snprintf(logpath, sizeof(logpath), "%s/data/admin-desktop.log", kProjectRoot);

    if (access(python, X_OK) != 0) {
        alert("找不到 .venv，请先在项目目录安装依赖后重装 App。");
        return 1;
    }

    if (chdir(kProjectRoot) != 0) {
        alert("无法进入项目目录。");
        return 1;
    }

    setenv("PYTHONPATH", kProjectRoot, 1);

    snprintf(
        shell_cmd,
        sizeof(shell_cmd),
        "arch -arm64 '%s' -m app.admin.desktop >>'%s' 2>&1",
        python,
        logpath
    );

    execl("/bin/bash", "bash", "-c", shell_cmd, NULL);

    alert("启动失败，请查看 data/admin-desktop.log。");
    return errno ? errno : 127;
}
