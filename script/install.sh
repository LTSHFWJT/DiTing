#!/bin/bash
# -----------------------------------------------------------------------------
# Main Linux KVM/QEMU 自动化安装脚本
# 支持 Debian / Ubuntu / CentOS / RHEL / Rocky / AlmaLinux / Fedora
# 功能: 安装 KVM + QEMU + libvirt，启用嵌套虚拟化，并配置用户权限
# -----------------------------------------------------------------------------

set -e  # 遇到错误立即退出

# 颜色输出
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'


print_info() {
    echo -e "${GREEN}[INFO]${NC} $1"
}

print_warning() {
    echo -e "${YELLOW}[WARN]${NC} $1"
}

print_error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

# 检测发行版
detect_os() {
    if [ -f /etc/os-release ]; then
        . /etc/os-release
        OS=$ID
        VER=$VERSION_ID
    else
        print_error "无法检测操作系统版本。"
        exit 1
    fi
    print_info "检测到操作系统: $OS $VER"
}

# 检查 CPU 虚拟化支持
check_cpu_virt() {
    print_info "检查CPU虚拟化支持..."
    if egrep -c '(vmx|svm)' /proc/cpuinfo > /dev/null; then
        print_info "CPU 支持硬件虚拟化。"
    else
        print_error "CPU 不支持硬件虚拟化，无法运行 KVM。"
        exit 1
    fi
}

# 启用嵌套虚拟化 (Intel 或 AMD)
enable_nested_virt() {
    print_info "启用嵌套虚拟化..."

    CPU_VENDOR=$(lscpu | grep "Vendor ID" | awk '{print $3}')
    KVM_INTEL_CONF="/etc/modprobe.d/kvm_intel.conf"
    KVM_AMD_CONF="/etc/modprobe.d/kvm_amd.conf"

    if [[ "$CPU_VENDOR" == "GenuineIntel" ]]; then
        if [ ! -f "$KVM_INTEL_CONF" ]; then
            echo "options kvm_intel nested=1" | sudo tee "$KVM_INTEL_CONF" > /dev/null
        else
            sudo sed -i 's/^options kvm_intel.*/options kvm_intel nested=1/' "$KVM_INTEL_CONF"
        fi
        print_info "Intel CPU，已配置嵌套虚拟化。"
    elif [[ "$CPU_VENDOR" == "AuthenticAMD" ]]; then
        if [ ! -f "$KVM_AMD_CONF" ]; then
            echo "options kvm_amd nested=1" | sudo tee "$KVM_AMD_CONF" > /dev/null
        else
            sudo sed -i 's/^options kvm_amd.*/options kvm_amd nested=1/' "$KVM_AMD_CONF"
        fi
        print_info "AMD CPU，已配置嵌套虚拟化。"
    else
        print_warning "未知 CPU 类型，跳过嵌套虚拟化配置。"
        return
    fi

    # 卸载并重新加载 KVM 模块
    sudo modprobe -r kvm_intel 2>/dev/null || sudo modprobe -r kvm_amd 2>/dev/null
    sudo modprobe -r kvm 2>/dev/null
    sudo modprobe kvm
    sudo modprobe kvm_intel 2>/dev/null || sudo modprobe kvm_amd 2>/dev/null

    # 验证嵌套虚拟化是否生效
    if [[ "$CPU_VENDOR" == "GenuineIntel" ]]; then
        if [ -f /sys/module/kvm_intel/parameters/nested ]; then
            if [ "$(cat /sys/module/kvm_intel/parameters/nested)" == "1" ]; then
                print_info "Intel 嵌套虚拟化已成功启用。"
            else
                print_warning "Intel 嵌套虚拟化可能未生效。"
            fi
        fi
    elif [[ "$CPU_VENDOR" == "AuthenticAMD" ]]; then
        if [ -f /sys/module/kvm_amd/parameters/nested ]; then
            if [ "$(cat /sys/module/kvm_amd/parameters/nested)" == "1" ]; then
                print_info "AMD 嵌套虚拟化已成功启用。"
            else
                print_warning "AMD 嵌套虚拟化可能未生效。"
            fi
        fi
    fi
}

# 根据发行版安装 KVM/QEMU/libvirt
install_packages() {
    print_info "开始安装 KVM/QEMU/libvirt 软件包..."
    case "$OS" in
        ubuntu|debian)
            sudo apt update
            sudo apt install -y qemu-kvm libvirt-daemon-system libvirt-clients virtinst bridge-utils cpu-checker
            ;;
        centos|rhel|rocky|almalinux)
            sudo dnf install -y epel-release
            sudo dnf install -y qemu-kvm libvirt virt-install bridge-utils
            ;;
        fedora)
            sudo dnf install -y qemu-kvm libvirt virt-install virt-viewer bridge-utils
            ;;
        *)
            print_error "不支持的发行版: $OS"
            exit 1
            ;;
    esac
    print_info "软件包安装完成。"
}

# 启动服务和配置权限
configure_services() {
    print_info "启动 libvirtd 服务..."
    sudo systemctl enable --now libvirtd

    print_info "将当前用户添加到 kvm 和 libvirt 组..."
    sudo usermod -aG kvm $USER
    sudo usermod -aG libvirt $USER

    print_warning "组权限更改生效需要重新登录或执行 'newgrp libvirt'。"
}

# 验证安装结果
verify_installation() {
    print_info "验证安装..."
    if command -v virsh &> /dev/null; then
        print_info "virsh 已安装。"
    else
        print_error "virsh 未安装，安装可能失败。"
        exit 1
    fi

    if virsh list --all > /dev/null 2>&1; then
        print_info "libvirtd 服务正常运行。"
    else
        print_error "libvirtd 服务未正确运行。"
        exit 1
    fi

    if command -v kvm-ok &> /dev/null; then
        if kvm-ok > /dev/null 2>&1; then
            print_info "KVM 加速可用。"
        else
            print_warning "KVM 加速不可用，请检查 BIOS 虚拟化设置。"
        fi
    else
        print_warning "kvm-ok 命令未找到，跳过 KVM 加速检查。"
    fi

    if [ -e /dev/kvm ]; then
        print_info "/dev/kvm 设备存在。"
    else
        print_error "/dev/kvm 设备不存在，KVM 无法使用。"
        exit 1
    fi
}

# 显示后续操作提示
show_next_steps() {
    echo ""
    print_info "==================================="
    print_info "   KVM 安装和配置成功完成！"
    print_info "==================================="
    echo ""
    print_info "后续步骤:"
    echo "  1. 为了组权限生效，请执行命令: newgrp libvirt"
    echo "     或者直接注销并重新登录系统。"
    echo "  2. 验证 KVM 环境:"
    echo "     virsh list --all"
    echo "  3. 安装 virt-manager 图形管理工具 (可选):"
    if [[ "$OS" == "ubuntu" || "$OS" == "debian" ]]; then
        echo "     sudo apt install virt-manager"
    else
        echo "     sudo dnf install virt-manager"
    fi
    echo ""
}

# 主函数
main() {
    if [[ $EUID -eq 0 ]]; then
        print_error "请不要使用 root 用户直接运行此脚本。"
        exit 1
    fi

    detect_os
    check_cpu_virt
    install_packages
    enable_nested_virt
    configure_services
    verify_installation
    show_next_steps
}

main "$@"
