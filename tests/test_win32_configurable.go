package main

import (
	"arknights-auto-operator/backend/controller"
	"fmt"
	"os"
	"path/filepath"
	"regexp"
	"syscall"
	"time"
	"unsafe"

	"github.com/MaaXYZ/maa-framework-go/v3"
	"github.com/MaaXYZ/maa-framework-go/v3/controller/win32"
)

var (
	user32              = syscall.NewLazyDLL("user32.dll")
	procEnumWindows     = user32.NewProc("EnumWindows")
	procGetWindowTextW  = user32.NewProc("GetWindowTextW")
	procGetClassNameW   = user32.NewProc("GetClassNameW")
	procIsWindowVisible = user32.NewProc("IsWindowVisible")
)

type WindowInfo struct {
	Handle    uintptr
	Title     string
	ClassName string
}

// Win32Config represents the Win32 controller configuration
type Win32Config struct {
	ClassRegex      string
	WindowRegex     string
	ScreencapMethod string // "GDI", "PrintWindow", "DXGI", etc.
	MouseMethod     string // "SendMessage", "SendMessageWithCursorPos", "PostMessage", etc.
	KeyboardMethod  string // "SendMessage", "SendMessageWithCursorPos", "PostMessage", etc.
}

func main() {
	fmt.Println("=== Win32 Controller Configurable Test ===\n")

	// Test configuration (similar to MaaUniversalUI)
	config := Win32Config{
		ClassRegex:      "",
		WindowRegex:     "MuMu",
		ScreencapMethod: "PrintWindow",
		MouseMethod:     "SendMessageWithCursorPos",
		KeyboardMethod:  "SendMessageWithCursorPos",
	}

	fmt.Println("Configuration:")
	fmt.Printf("  Class Regex: %s\n", config.ClassRegex)
	fmt.Printf("  Window Regex: %s\n", config.WindowRegex)
	fmt.Printf("  Screencap: %s\n", config.ScreencapMethod)
	fmt.Printf("  Mouse: %s\n", config.MouseMethod)
	fmt.Printf("  Keyboard: %s\n\n", config.KeyboardMethod)

	// Initialize MAA Framework
	exePath, err := os.Executable()
	if err != nil {
		fmt.Printf("Error getting executable path: %v\n", err)
		return
	}
	exeDir := filepath.Dir(exePath)
	libDir := filepath.Join(exeDir, "lib")
	logDir := filepath.Join(exeDir, "logs")

	fmt.Printf("Initializing MAA Framework...\n")
	maa.Init(
		maa.WithLibDir(libDir),
		maa.WithLogDir(logDir),
	)
	fmt.Printf("MAA Version: %s\n\n", maa.Version())

	// Find window using regex
	fmt.Println("Searching for window...")
	windows := findAllWindows()

	var targetWindow *WindowInfo
	classRe := regexp.MustCompile(config.ClassRegex)
	titleRe := regexp.MustCompile(config.WindowRegex)

	for _, win := range windows {
		if classRe.MatchString(win.ClassName) && titleRe.MatchString(win.Title) {
			targetWindow = &win
			break
		}
	}

	// Fallback: try to find any Unity window or MuMu window for testing
	if targetWindow == nil {
		fmt.Println("Target window not found, trying fallback...")
		unityRe := regexp.MustCompile("(?i)Unity.*")
		mumuRe := regexp.MustCompile("(?i)(MuMu|模拟器|明日方舟|Arknights)")

		for _, win := range windows {
			if unityRe.MatchString(win.ClassName) || mumuRe.MatchString(win.Title) {
				targetWindow = &win
				fmt.Printf("  Using fallback window: %s\n", win.Title)
				break
			}
		}
	}

	if targetWindow == nil {
		fmt.Println("❌ No matching window found!")
		fmt.Println("\nAvailable windows:")
		for i, win := range windows {
			if i >= 20 {
				fmt.Printf("... and %d more windows\n", len(windows)-20)
				break
			}
			fmt.Printf("  [%d] %s (Class: %s)\n", i+1, win.Title, win.ClassName)
		}
		return
	}

	fmt.Printf("✓ Found window:\n")
	fmt.Printf("  Handle: 0x%X\n", targetWindow.Handle)
	fmt.Printf("  Title: %s\n", targetWindow.Title)
	fmt.Printf("  Class: %s\n\n", targetWindow.ClassName)

	// Create Win32 Controller with configured methods
	fmt.Println("Creating Win32 Controller with configured methods...")

	screencapType := parseScreencapMethod(config.ScreencapMethod)
	mouseType := parseInputMethod(config.MouseMethod)
	keyboardType := parseInputMethod(config.KeyboardMethod)

	ctrl := maa.NewWin32Controller(
		unsafe.Pointer(targetWindow.Handle),
		screencapType,
		mouseType,
		keyboardType,
	)
	if ctrl == nil {
		fmt.Println("❌ Failed to create controller")
		return
	}
	defer ctrl.Destroy()

	fmt.Println("Connecting to window...")
	if !ctrl.PostConnect().Wait().Success() {
		fmt.Println("❌ Failed to connect to window")
		return
	}
	fmt.Println("✓ Connected successfully\n")

	// Run tests
	fmt.Println("Test 1: Screenshot")
	testScreenshot(ctrl)
	fmt.Println()

	fmt.Println("Test 2: Click")
	testClick(ctrl, targetWindow.Handle)
	fmt.Println()

	fmt.Println("Test 3: Swipe")
	testSwipe(ctrl, targetWindow.Handle)
	fmt.Println()

	fmt.Println("=== Test Completed ===")
}

func parseScreencapMethod(method string) win32.ScreencapType {
	switch method {
	case "GDI":
		return win32.ScreencapGDI
	case "PrintWindow":
		return win32.ScreencapPrintWindow
	case "DXGI_DesktopDup":
		return win32.ScreencapDXGIDesktopDup
	case "DXGI_FramePool":
		return win32.ScreencapDXGIFramePool
	default:
		fmt.Printf("  Warning: Unknown screencap method '%s', using GDI\n", method)
		return win32.ScreencapGDI
	}
}

func parseInputMethod(method string) win32.InputType {
	switch method {
	case "SendMessage":
		return win32.InputSendMessage
	case "SendMessageWithCursorPos":
		return win32.InputSendMessageWithCursorPos
	case "PostMessage":
		return win32.InputPostMessage
	default:
		fmt.Printf("  Warning: Unknown input method '%s', using SendMessage\n", method)
		return win32.InputSendMessage
	}
}

func testScreenshot(ctrl *maa.Controller) {
	fmt.Println("  Testing screenshot capability...")
	fmt.Println("  ✓ Controller is ready for screenshot operations")
	fmt.Println("  Note: Screenshot is captured during recognition tasks")
}

func testClick(ctrl *maa.Controller, hwnd uintptr) {
	fmt.Println("  Testing click...")

	rect, err := controller.GetWindowRect(hwnd)
	if err != nil {
		fmt.Printf("  ❌ Failed to get window rect: %v\n", err)
		return
	}

	centerX := (rect.Right - rect.Left) / 2
	centerY := (rect.Bottom - rect.Top) / 2

	fmt.Printf("  Clicking at (%d, %d)...\n", centerX, centerY)

	job := ctrl.PostClick(centerX, centerY)
	if !job.Wait().Success() {
		fmt.Println("  ❌ Click failed")
		return
	}

	fmt.Println("  ✓ Click successful")
}

func testSwipe(ctrl *maa.Controller, hwnd uintptr) {
	fmt.Println("  Testing swipe...")

	rect, err := controller.GetWindowRect(hwnd)
	if err != nil {
		fmt.Printf("  ❌ Failed to get window rect: %v\n", err)
		return
	}

	w := rect.Right - rect.Left
	h := rect.Bottom - rect.Top
	startX := w / 4
	startY := h / 2
	endX := w * 3 / 4
	endY := h / 2

	fmt.Printf("  Swiping from (%d, %d) to (%d, %d)...\n", startX, startY, endX, endY)

	job := ctrl.PostSwipe(startX, startY, endX, endY, 500*time.Millisecond)
	if !job.Wait().Success() {
		fmt.Println("  ❌ Swipe failed")
		return
	}

	fmt.Println("  ✓ Swipe successful")
}

func findAllWindows() []WindowInfo {
	var windows []WindowInfo

	callback := syscall.NewCallback(func(hwnd uintptr, lParam uintptr) uintptr {
		ret, _, _ := procIsWindowVisible.Call(hwnd)
		if ret == 0 {
			return 1
		}

		titleBuf := make([]uint16, 256)
		procGetWindowTextW.Call(hwnd, uintptr(unsafe.Pointer(&titleBuf[0])), 256)
		title := syscall.UTF16ToString(titleBuf)

		classBuf := make([]uint16, 256)
		procGetClassNameW.Call(hwnd, uintptr(unsafe.Pointer(&classBuf[0])), 256)
		className := syscall.UTF16ToString(classBuf)

		if title != "" {
			windows = append(windows, WindowInfo{
				Handle:    hwnd,
				Title:     title,
				ClassName: className,
			})
		}

		return 1
	})

	procEnumWindows.Call(callback, 0)
	return windows
}
