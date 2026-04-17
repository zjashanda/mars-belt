#!/usr/bin/env bash

set -e

#################################
# 配置
#################################

CTRL_PORT="/dev/ttyACM4"
BURN_PORT="/dev/ttyACM0"

BURN_TOOL="./Uart_Burn_Tool"
FW_FILE="app.bin"

BAUD=460800
MAX_RETRY=3

LOG_DIR="../../artifacts/burn"
mkdir -p "$LOG_DIR"

LOG_FILE="$LOG_DIR/burn_$(date +%Y%m%d_%H%M%S).log"

#################################
# 工具函数
#################################

log()
{
    echo "[$(date '+%H:%M:%S')] $*" | tee -a "$LOG_FILE"
}


sha256_file()
{
    local file=$1

    if command -v sha256sum >/dev/null 2>&1
    then
        sha256sum "$file" | awk '{print $1}'
        return 0
    fi

    if command -v shasum >/dev/null 2>&1
    then
        shasum -a 256 "$file" | awk '{print $1}'
        return 0
    fi

    return 1
}


preflight_fw()
{
    local size
    local sha256

    if [ ! -e "$FW_FILE" ]
    then
        log "固件文件不存在: $FW_FILE"
        return 1
    fi

    if [ ! -s "$FW_FILE" ]
    then
        log "固件文件为空: $FW_FILE"
        return 1
    fi

    size=$(stat -c%s "$FW_FILE" 2>/dev/null || stat -f%z "$FW_FILE" 2>/dev/null || echo "unknown")
    log "固件预检通过 path=$FW_FILE size=$size"

    if sha256=$(sha256_file "$FW_FILE" 2>/dev/null)
    then
        log "固件SHA256=$sha256"
    else
        log "未找到 sha256 计算工具，跳过哈希记录"
    fi
}

#################################
# 等待串口
#################################

wait_port()
{
    local port=$1

    for i in {1..20}
    do
        if [ -e "$port" ]; then
            log "检测到串口 $port"
            return 0
        fi
        sleep 0.5
    done

    log "等待串口 $port 超时"
    return 1
}

#################################
# 串口发送命令
#################################

send_cmd()
{
    local port=$1
    local cmd=$2

    log "发送命令 -> $port : $cmd"

    printf "%s\r\n" "$cmd" > "$port"

    sleep 0.3
}

#################################
# 进入烧录模式
#################################

enter_burn_mode()
{
    log "进入烧录模式"

    send_cmd "$CTRL_PORT" "uut-switch1.off"
    send_cmd "$CTRL_PORT" "uut-switch2.on"
    send_cmd "$CTRL_PORT" "uut-switch1.on"
    send_cmd "$CTRL_PORT" "uut-switch2.off"

    sleep 2
}

#################################
# 退出烧录模式
#################################

exit_burn_mode()
{
    log "退出烧录模式"
    send_cmd "$CTRL_PORT" "uut-switch2.off"
    send_cmd "$CTRL_PORT" "uut-switch1.off"
    send_cmd "$CTRL_PORT" "uut-switch1.on"

    sleep 2
}



#################################
# 烧录
#################################

burn_fw()
{
    log "开始烧录固件 $FW_FILE"

    sudo "$BURN_TOOL" \
        -b "$BAUD" \
        -p "$BURN_PORT" \
        -f "$FW_FILE" \
        -m -d -a 0x0 \
        -i adaptive-duplex \
        -s | tee -a "$LOG_FILE"

    if grep -q "SEND END COMMAND SUCCESS" "$LOG_FILE" &&
       grep -q "SEND MD5 COMMAND WITH RAM SUCCESS" "$LOG_FILE" &&
       grep -q "CONNECT ROM AND DOWNLOAD RAM LOADER SUCCESS" "$LOG_FILE"
    then
        log "烧录成功"
		exit_burn_mode
        return 0
    else
        log "烧录日志未检测到成功标志"
        return 1
    fi
}

#################################
# 设置日志等级
#################################

set_loglevel()
{
	sleep 5
    log "设置 loglevel 4"

    printf "loglevel 4\r\n" > "$BURN_PORT"

    sleep 1
}

#################################
# 主流程
#################################

log "========== 烧录流程开始 =========="

preflight_fw
wait_port "$CTRL_PORT"
wait_port "$BURN_PORT"

retry=0

while [ $retry -lt $MAX_RETRY ]
do

    log "烧录尝试 $((retry+1))"

    enter_burn_mode

    if burn_fw
    then
        set_loglevel

        log "🎉 烧录流程完成"
        exit 0
    fi

    log "烧录失败，准备重试"

    retry=$((retry+1))

    sleep 2

done

log "❌ 烧录最终失败"

exit 1
