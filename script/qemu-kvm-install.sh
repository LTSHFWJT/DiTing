#!/usr/bin/env bash
# -----------------------------------------------------------------------------
# DiTing Linux KVM/QEMU 自动化安装脚本
#
# 目标:
#   - 尽量使用发行版自带包管理器安装 QEMU/KVM/libvirt/virt-install
#   - 适配主流 Linux: Debian/Ubuntu 系、RHEL/Fedora 系、openSUSE、
#     Arch/Manjaro、Alpine
#   - 参考 CAPEv2 installer/kvm-qemu.sh 的主机配置思路:
#     KVM 权限、libvirt 服务、bridge sysctl、nested virt、ignore_msrs
# -----------------------------------------------------------------------------

set -Eeuo pipefail

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

OS_ID=""
OS_LIKE=""
OS_VERSION=""
PM=""
TARGET_USER=""
WITH_GUI=0
ENABLE_NESTED=1
START_SERVICES=1
SKIP_CPU_CHECK=0
HARDWARE_READY=1

print_info() {
    echo -e "${GREEN}[INFO]${NC} $1"
}

print_warning() {
    echo -e "${YELLOW}[WARN]${NC} $1"
}

print_error() {
    echo -e "${RED}[ERROR]${NC} $1" >&2
}

usage() {
    cat <<'EOF'
用法:
  ./script/install.sh [选项]

选项:
  --with-gui          同时安装 virt-manager/virt-viewer 等图形管理工具
  --user USER         指定需要加入 kvm/libvirt 组的普通用户
  --no-nested         不配置嵌套虚拟化
  --no-start          只安装软件包，不启动/启用 libvirt 服务
  --skip-cpu-check    跳过 CPU 虚拟化能力检查
  -h, --help          显示帮助

示例:
  sudo ./script/install.sh --user diting
  ./script/install.sh --with-gui
EOF
}

parse_args() {
    while [ $# -gt 0 ]; do
        case "$1" in
            --with-gui)
                WITH_GUI=1
                ;;
            --user)
                if [ $# -lt 2 ] || [ -z "${2:-}" ]; then
                    print_error "--user 需要一个用户名"
                    exit 1
                fi
                TARGET_USER="$2"
                shift
                ;;
            --no-nested)
                ENABLE_NESTED=0
                ;;
            --no-start)
                START_SERVICES=0
                ;;
            --skip-cpu-check)
                SKIP_CPU_CHECK=1
                ;;
            -h|--help)
                usage
                exit 0
                ;;
            *)
                print_error "未知参数: $1"
                usage
                exit 1
                ;;
        esac
        shift
    done
}

as_root() {
    if [ "$EUID" -eq 0 ]; then
        "$@"
    else
        sudo "$@"
    fi
}

have_cmd() {
    command -v "$1" >/dev/null 2>&1
}

require_privilege() {
    if [ "$EUID" -ne 0 ]; then
        if ! have_cmd sudo; then
            print_error "需要 root 权限，且当前系统没有 sudo。请使用 root 执行。"
            exit 1
        fi
        sudo -v
    fi
}

detect_target_user() {
    if [ -n "$TARGET_USER" ]; then
        return
    fi

    if [ -n "${SUDO_USER:-}" ] && [ "${SUDO_USER:-}" != "root" ]; then
        TARGET_USER="$SUDO_USER"
    elif [ -n "${USER:-}" ] && [ "${USER:-}" != "root" ]; then
        TARGET_USER="$USER"
    else
        TARGET_USER=""
    fi
}

detect_os() {
    if [ ! -r /etc/os-release ]; then
        print_error "无法读取 /etc/os-release，不能判断 Linux 发行版。"
        exit 1
    fi

    # shellcheck disable=SC1091
    . /etc/os-release
    OS_ID="${ID:-unknown}"
    OS_LIKE="${ID_LIKE:-}"
    OS_VERSION="${VERSION_ID:-unknown}"

    print_info "检测到系统: ${PRETTY_NAME:-$OS_ID $OS_VERSION}"
}

detect_package_manager() {
    if have_cmd apt-get; then
        PM="apt"
    elif have_cmd dnf; then
        PM="dnf"
    elif have_cmd yum; then
        PM="yum"
    elif have_cmd zypper; then
        PM="zypper"
    elif have_cmd pacman; then
        PM="pacman"
    elif have_cmd apk; then
        PM="apk"
    else
        print_error "未找到受支持的包管理器: apt-get/dnf/yum/zypper/pacman/apk"
        exit 1
    fi

    print_info "使用包管理器: $PM"
}

check_cpu_virt() {
    if [ "$SKIP_CPU_CHECK" -eq 1 ]; then
        print_warning "已跳过 CPU 虚拟化能力检查。"
        return
    fi

    print_info "检查 CPU 硬件虚拟化支持..."
    if [ -r /proc/cpuinfo ] && grep -Eq '(vmx|svm)' /proc/cpuinfo; then
        print_info "CPU 支持硬件虚拟化。"
        return
    fi

    print_error "CPU 未暴露 vmx/svm 标志，KVM 可能无法运行。请检查 BIOS/UEFI 虚拟化开关。"
    exit 1
}

pm_update() {
    case "$PM" in
        apt)
            as_root apt-get update
            ;;
        dnf)
            as_root dnf makecache
            ;;
        yum)
            as_root yum makecache
            ;;
        zypper)
            as_root zypper --non-interactive refresh
            ;;
        pacman)
            as_root pacman -Syu --noconfirm
            ;;
        apk)
            as_root apk update
            ;;
    esac
}

pm_install() {
    case "$PM" in
        apt)
            as_root env DEBIAN_FRONTEND=noninteractive apt-get install -y "$@"
            ;;
        dnf)
            as_root dnf install -y "$@"
            ;;
        yum)
            as_root yum install -y "$@"
            ;;
        zypper)
            as_root zypper --non-interactive --auto-agree-with-licenses install "$@"
            ;;
        pacman)
            as_root pacman -S --needed --noconfirm "$@"
            ;;
        apk)
            as_root apk add --no-cache "$@"
            ;;
    esac
}

pm_install_optional() {
    local pkg

    for pkg in "$@"; do
        if pm_install "$pkg"; then
            print_info "已安装/已存在可选包: $pkg"
        else
            print_warning "跳过不可用或安装失败的可选包: $pkg"
        fi
    done
}

pm_install_first_available() {
    local label="$1"
    shift

    local pkg
    for pkg in "$@"; do
        if pm_install "$pkg"; then
            print_info "$label 使用包: $pkg"
            return
        fi
    done

    print_error "无法安装 $label，已尝试: $*"
    exit 1
}

zypper_install_pattern_optional() {
    local pattern

    for pattern in "$@"; do
        if as_root zypper --non-interactive --auto-agree-with-licenses install -t pattern "$pattern"; then
            print_info "已安装/已存在 openSUSE pattern: $pattern"
        else
            print_warning "跳过不可用或安装失败的 openSUSE pattern: $pattern"
        fi
    done
}

install_packages() {
    print_info "刷新软件包索引..."
    pm_update

    print_info "安装 KVM/QEMU/libvirt 基础组件..."
    case "$PM" in
        apt)
            pm_install \
                qemu-system-x86 \
                qemu-utils \
                libvirt-daemon-system \
                libvirt-clients
            pm_install_first_available "virt-install" virt-install virtinst
            pm_install_optional \
                qemu-kvm \
                bridge-utils \
                dnsmasq-base \
                ovmf \
                cpu-checker \
                ebtables \
                iptables \
                libguestfs-tools
            if [ "$WITH_GUI" -eq 1 ]; then
                pm_install_optional virt-manager virt-viewer
            fi
            ;;
        dnf|yum)
            pm_install \
                qemu-kvm \
                libvirt \
                libvirt-daemon-kvm \
                libvirt-client \
                virt-install \
                dnsmasq
            pm_install_optional \
                bridge-utils \
                edk2-ovmf \
                iptables \
                libguestfs-tools \
                libvirt-daemon-driver-qemu \
                swtpm \
                virt-viewer
            if [ "$WITH_GUI" -eq 1 ]; then
                pm_install_optional virt-manager
            fi
            ;;
        zypper)
            zypper_install_pattern_optional kvm_server kvm_tools
            pm_install \
                qemu \
                libvirt \
                virt-install \
                dnsmasq
            pm_install_optional \
                libvirt-daemon-driver-qemu \
                libvirt-daemon-qemu \
                qemu-kvm \
                qemu-tools \
                bridge-utils \
                qemu-ovmf-x86_64 \
                ovmf \
                patterns-server-kvm_server \
                patterns-server-kvm_tools
            if [ "$WITH_GUI" -eq 1 ]; then
                pm_install_optional virt-manager virt-viewer
            fi
            ;;
        pacman)
            pm_install_first_available "QEMU" qemu-full qemu-desktop qemu-base
            pm_install \
                libvirt \
                dnsmasq \
                bridge-utils \
                openbsd-netcat \
                iptables-nft \
                edk2-ovmf
            pm_install_optional \
                virt-install \
                libguestfs \
                swtpm \
                virt-viewer
            if [ "$WITH_GUI" -eq 1 ]; then
                pm_install_optional virt-manager
            fi
            ;;
        apk)
            pm_install \
                qemu-system-x86_64 \
                qemu-img \
                libvirt \
                libvirt-client \
                libvirt-daemon-openrc \
                libvirt-qemu \
                bridge-utils \
                dnsmasq \
                iptables \
                ovmf
            pm_install_optional virt-install libguestfs ebtables
            if [ "$WITH_GUI" -eq 1 ]; then
                pm_install_optional virt-manager virt-viewer
            fi
            ;;
    esac

    print_info "软件包安装阶段完成。"
}

detect_cpu_vendor() {
    if have_cmd lscpu; then
        lscpu | awk -F: '/Vendor ID/ {gsub(/^[ \t]+/, "", $2); print $2; exit}'
        return
    fi

    awk -F: '/vendor_id/ {gsub(/^[ \t]+/, "", $2); print $2; exit}' /proc/cpuinfo 2>/dev/null || true
}

enable_nested_virt() {
    if [ "$ENABLE_NESTED" -eq 0 ]; then
        print_warning "按参数要求跳过嵌套虚拟化配置。"
        return
    fi

    local cpu_vendor
    local module_name
    local conf_file
    local nested_value

    cpu_vendor="$(detect_cpu_vendor)"
    case "$cpu_vendor" in
        GenuineIntel)
            module_name="kvm_intel"
            conf_file="/etc/modprobe.d/kvm_intel.conf"
            ;;
        AuthenticAMD)
            module_name="kvm_amd"
            conf_file="/etc/modprobe.d/kvm_amd.conf"
            ;;
        *)
            print_warning "无法识别 CPU 厂商($cpu_vendor)，跳过嵌套虚拟化配置。"
            return
            ;;
    esac

    print_info "配置 ${module_name} 嵌套虚拟化..."
    as_root mkdir -p /etc/modprobe.d
    printf 'options %s nested=1\n' "$module_name" | as_root tee "$conf_file" >/dev/null

    if as_root modprobe "$module_name" 2>/dev/null; then
        print_info "已加载 $module_name 模块。"
    else
        print_warning "无法立即加载 $module_name；如模块正在使用，重启后会按配置生效。"
    fi

    if [ -r "/sys/module/${module_name}/parameters/nested" ]; then
        nested_value="$(cat "/sys/module/${module_name}/parameters/nested")"
        case "$nested_value" in
            1|Y|y)
                print_info "嵌套虚拟化已启用。"
                ;;
            *)
                print_warning "嵌套虚拟化当前值为 $nested_value，可能需要重启。"
                ;;
        esac
    else
        print_warning "未找到 /sys/module/${module_name}/parameters/nested，可能需要重启后验证。"
    fi
}

configure_kvm_params() {
    print_info "配置 KVM 兼容性参数..."

    as_root mkdir -p /etc/modprobe.d
    cat <<'EOF' | as_root tee /etc/modprobe.d/kvm.conf >/dev/null
options kvm ignore_msrs=Y
options kvm report_ignored_msrs=N
EOF

    if [ -e /sys/module/kvm/parameters/ignore_msrs ]; then
        echo 1 | as_root tee /sys/module/kvm/parameters/ignore_msrs >/dev/null || true
    fi

    if [ -e /sys/module/kvm/parameters/report_ignored_msrs ]; then
        echo 0 | as_root tee /sys/module/kvm/parameters/report_ignored_msrs >/dev/null || true
    fi
}

configure_bridge_sysctl() {
    print_info "配置 libvirt bridge 网络 sysctl 参数..."

    as_root modprobe br_netfilter 2>/dev/null || true
    as_root mkdir -p /etc/sysctl.d
    cat <<'EOF' | as_root tee /etc/sysctl.d/99-diting-kvm.conf >/dev/null
net.bridge.bridge-nf-call-ip6tables = 0
net.bridge.bridge-nf-call-iptables = 0
net.bridge.bridge-nf-call-arptables = 0
EOF

    as_root sysctl --system >/dev/null 2>&1 || print_warning "sysctl --system 未完全成功，bridge 参数可能需要重启后生效。"
}

group_exists() {
    local group_name="$1"

    if have_cmd getent; then
        getent group "$group_name" >/dev/null 2>&1
    else
        grep -q "^${group_name}:" /etc/group 2>/dev/null
    fi
}

ensure_group() {
    local group_name="$1"

    group_exists "$group_name" && return

    if have_cmd groupadd; then
        as_root groupadd "$group_name" || true
    elif have_cmd addgroup; then
        as_root addgroup -S "$group_name" 2>/dev/null || as_root addgroup "$group_name" || true
    fi
}

add_target_user_to_group() {
    local group_name="$1"

    if [ -z "$TARGET_USER" ]; then
        return
    fi

    if ! id "$TARGET_USER" >/dev/null 2>&1; then
        print_warning "用户 $TARGET_USER 不存在，跳过加入 $group_name 组。"
        return
    fi

    if ! group_exists "$group_name"; then
        return
    fi

    if have_cmd usermod; then
        as_root usermod -aG "$group_name" "$TARGET_USER"
    elif have_cmd addgroup; then
        as_root addgroup "$TARGET_USER" "$group_name" || true
    fi
}

configure_kvm_permissions() {
    print_info "配置 /dev/kvm 权限和用户组..."

    ensure_group kvm
    add_target_user_to_group kvm
    add_target_user_to_group libvirt
    add_target_user_to_group libvirtd

    if [ -e /dev/kvm ]; then
        as_root chgrp kvm /dev/kvm 2>/dev/null || true
        as_root chmod 0660 /dev/kvm 2>/dev/null || true
    else
        print_warning "/dev/kvm 不存在；请检查 BIOS/UEFI 虚拟化开关、内核模块或宿主机是否支持 KVM。"
        HARDWARE_READY=0
    fi

    as_root mkdir -p /etc/udev/rules.d
    cat <<'EOF' | as_root tee /etc/udev/rules.d/50-qemu-kvm.rules >/dev/null
KERNEL=="kvm", GROUP="kvm", MODE="0660"
EOF

    if have_cmd udevadm; then
        as_root udevadm control --reload-rules 2>/dev/null || true
        as_root udevadm trigger --name-match=kvm 2>/dev/null || true
    fi

    if [ -n "$TARGET_USER" ]; then
        print_warning "用户组变更需要重新登录，或临时执行: newgrp libvirt"
    else
        print_warning "未识别到普通用户；如需非 root 使用 libvirt，请手动执行: usermod -aG kvm,libvirt <user>"
    fi
}

systemd_unit_exists() {
    local unit="$1"
    systemctl list-unit-files "$unit" 2>/dev/null | awk '{print $1}' | grep -Fxq "$unit"
}

start_systemd_units() {
    local unit
    local started=0

    for unit in \
        virtqemud.service \
        virtnetworkd.service \
        virtstoraged.service \
        virtlogd.socket \
        virtqemud.socket \
        libvirtd.service
    do
        if systemd_unit_exists "$unit"; then
            if as_root systemctl enable --now "$unit"; then
                started=1
            else
                print_warning "启动 $unit 失败，继续尝试其他 libvirt 单元。"
            fi
        fi
    done

    if [ "$started" -eq 0 ]; then
        print_warning "没有找到可启用的 libvirt systemd 单元。"
    fi
}

start_openrc_services() {
    if have_cmd rc-update && have_cmd rc-service; then
        as_root rc-update add libvirtd default 2>/dev/null || true
        as_root rc-service libvirtd restart 2>/dev/null || true
        as_root rc-update add virtlogd default 2>/dev/null || true
        as_root rc-service virtlogd restart 2>/dev/null || true
        return
    fi

    if have_cmd service; then
        as_root service libvirtd restart 2>/dev/null || true
    fi
}

configure_libvirt_network() {
    if ! have_cmd virsh; then
        return
    fi

    if as_root virsh net-info default >/dev/null 2>&1; then
        as_root virsh net-autostart default >/dev/null 2>&1 || true
        as_root virsh net-start default >/dev/null 2>&1 || true
    else
        print_warning "未找到 libvirt default 网络；后续可用 virt-manager 或 virsh net-define 手动创建。"
    fi
}

configure_services() {
    if [ "$START_SERVICES" -eq 0 ]; then
        print_warning "按参数要求跳过 libvirt 服务启动。"
        return
    fi

    print_info "启动并启用 libvirt 服务..."
    if have_cmd systemctl && [ -d /run/systemd/system ]; then
        start_systemd_units
    else
        start_openrc_services
    fi

    configure_libvirt_network
}

verify_installation() {
    local qemu_cmd=""

    print_info "验证安装结果..."

    if have_cmd qemu-system-x86_64; then
        qemu_cmd="$(command -v qemu-system-x86_64)"
    elif have_cmd qemu-kvm; then
        qemu_cmd="$(command -v qemu-kvm)"
    fi

    if [ -n "$qemu_cmd" ]; then
        print_info "QEMU 可执行文件: $qemu_cmd"
    else
        print_error "未找到 qemu-system-x86_64 或 qemu-kvm。"
        exit 1
    fi

    if have_cmd virsh; then
        print_info "virsh 已安装。"
    else
        print_error "virsh 未安装，libvirt 客户端安装可能失败。"
        exit 1
    fi

    if [ "$START_SERVICES" -eq 1 ]; then
        if as_root virsh list --all >/dev/null 2>&1; then
            print_info "libvirt 连接正常。"
        else
            print_warning "virsh 暂时无法连接 libvirt；请检查 libvirt 服务状态。"
            HARDWARE_READY=0
        fi
    fi

    if [ -e /dev/kvm ]; then
        print_info "/dev/kvm 存在。"
    else
        print_warning "/dev/kvm 不存在，当前主机还不能使用 KVM 加速。"
        HARDWARE_READY=0
    fi

    if have_cmd kvm-ok; then
        if kvm-ok >/dev/null 2>&1; then
            print_info "kvm-ok 检查通过。"
        else
            print_warning "kvm-ok 检查未通过，请检查 BIOS/UEFI 虚拟化设置。"
            HARDWARE_READY=0
        fi
    fi

    if have_cmd virt-host-validate; then
        as_root virt-host-validate qemu || true
    fi
}

show_next_steps() {
    echo ""
    print_info "==================================="
    if [ "$HARDWARE_READY" -eq 1 ]; then
        print_info "KVM/QEMU/libvirt 安装和基础配置完成"
    else
        print_warning "软件安装完成，但硬件/服务验证仍有警告"
    fi
    print_info "==================================="
    echo ""
    print_info "后续操作:"
    echo "  1. 重新登录，让 kvm/libvirt 用户组权限生效。"
    echo "  2. 验证 libvirt:"
    echo "     virsh -c qemu:///system list --all"
    echo "  3. 验证 KVM:"
    echo "     test -e /dev/kvm && echo KVM ready"

    if [ "$WITH_GUI" -eq 0 ]; then
        echo "  4. 如需图形管理工具，可重新运行:"
        echo "     ./script/install.sh --with-gui"
    else
        echo "  4. 图形管理工具启动命令:"
        echo "     virt-manager"
    fi
    echo ""
}

main() {
    parse_args "$@"
    require_privilege
    detect_target_user
    detect_os
    detect_package_manager
    check_cpu_virt
    install_packages
    enable_nested_virt
    configure_kvm_params
    configure_bridge_sysctl
    configure_kvm_permissions
    configure_services
    verify_installation
    show_next_steps
}

main "$@"
