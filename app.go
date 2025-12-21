package main

import (
	"context"
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"regexp"
	"syscall"
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

// App struct
type App struct {
	ctx context.Context
}

// NewApp creates a new App application struct
func NewApp() *App {
	return &App{}
}

// Startup is called when the app starts
func (a *App) Startup(ctx context.Context) {
	a.ctx = ctx
}

// Win32Config represents the Win32 controller configuration
type Win32Config struct {
	ClassRegex  string `json:"class_regex"`
	WindowRegex string `json:"window_regex"`
	Screencap   string `json:"screencap"`
	Mouse       string `json:"mouse"`
	Keyboard    string `json:"keyboard"`
}

// TestResult represents the result of a connection test
type TestResult struct {
	Success     bool   `json:"success"`
	WindowTitle string `json:"window_title"`
	Error       string `json:"error"`
}

// WindowInfo represents window information
type WindowInfo struct {
	Handle    uintptr `json:"-"`
	Title     string  `json:"title"`
	ClassName string  `json:"class_name"`
}

// TestWin32Connection tests the Win32 connection with the given configuration
func (a *App) TestWin32Connection(config Win32Config) (result TestResult) {
	// Add panic recovery with stack trace
	defer func() {
		if r := recover(); r != nil {
			result = TestResult{
				Success: false,
				Error:   fmt.Sprintf("Panic occurred: %v\nThis usually means MAA Framework encountered an error. Please check:\n1. MAA Framework DLLs are in lib/ directory\n2. Window handle is valid\n3. MAA Framework version is compatible", r),
			}
		}
	}()

	// Initialize MAA Framework if not already initialized
	exePath, err := os.Executable()
	if err != nil {
		return TestResult{Success: false, Error: fmt.Sprintf("Failed to get executable path: %v", err)}
	}
	exeDir := filepath.Dir(exePath)

	// Try to find lib directory
	// First try: exeDir/lib (production mode)
	// Second try: exeDir/../../lib (development mode - build/bin/)
	libDir := filepath.Join(exeDir, "lib")
	if _, err := os.Stat(filepath.Join(libDir, "MaaFramework.dll")); os.IsNotExist(err) {
		// Try development mode path
		devLibDir := filepath.Join(exeDir, "..", "..", "lib")
		if _, err := os.Stat(filepath.Join(devLibDir, "MaaFramework.dll")); err == nil {
			libDir = devLibDir
		}
	}

	logDir := filepath.Join(exeDir, "logs")

	// Check if MAA Framework DLLs exist
	maaFrameworkPath := filepath.Join(libDir, "MaaFramework.dll")
	if _, err := os.Stat(maaFrameworkPath); os.IsNotExist(err) {
		return TestResult{Success: false, Error: fmt.Sprintf("MAA Framework not found: %s does not exist.\nPlease ensure MAA Framework DLLs are in the lib/ directory.", maaFrameworkPath)}
	}

	maa.Init(
		maa.WithLibDir(libDir),
		maa.WithLogDir(logDir),
	)

	// Find window using regex
	windows := findAllWindows()
	if len(windows) == 0 {
		return TestResult{Success: false, Error: "No windows found on system"}
	}

	classRe, err := regexp.Compile(config.ClassRegex)
	if err != nil {
		return TestResult{Success: false, Error: fmt.Sprintf("Invalid class regex: %v", err)}
	}

	titleRe, err := regexp.Compile(config.WindowRegex)
	if err != nil {
		return TestResult{Success: false, Error: fmt.Sprintf("Invalid window regex: %v", err)}
	}

	var targetWindow *WindowInfo
	for _, win := range windows {
		if classRe.MatchString(win.ClassName) && titleRe.MatchString(win.Title) {
			targetWindow = &win
			break
		}
	}

	if targetWindow == nil {
		return TestResult{Success: false, Error: fmt.Sprintf("No matching window found. Searched %d windows with class regex '%s' and title regex '%s'", len(windows), config.ClassRegex, config.WindowRegex)}
	}

	// Validate window handle
	if targetWindow.Handle == 0 {
		return TestResult{Success: false, Error: "Invalid window handle (0)"}
	}

	// Create Win32 Controller with configured methods
	screencapType := parseScreencapMethod(config.Screencap)
	mouseType := parseInputMethod(config.Mouse)
	keyboardType := parseInputMethod(config.Keyboard)

	// Try to create controller - this is where panic might occur
	var ctrl *maa.Controller
	func() {
		defer func() {
			if r := recover(); r != nil {
				ctrl = nil
			}
		}()
		ctrl = maa.NewWin32Controller(
			unsafe.Pointer(targetWindow.Handle),
			screencapType,
			mouseType,
			keyboardType,
		)
	}()

	if ctrl == nil {
		return TestResult{Success: false, Error: "Failed to create Win32 controller. This might be due to:\n1. Invalid window handle\n2. MAA Framework initialization failure\n3. Incompatible screencap/input methods"}
	}
	defer func() {
		if ctrl != nil {
			ctrl.Destroy()
		}
	}()

	// Test connection
	var job *maa.Job
	func() {
		defer func() {
			if r := recover(); r != nil {
				job = nil
			}
		}()
		job = ctrl.PostConnect()
	}()

	if job == nil {
		return TestResult{Success: false, Error: "PostConnect failed or returned nil"}
	}

	// Wait for connection to complete
	func() {
		defer func() {
			if r := recover(); r != nil {
				job = nil
			}
		}()
		job = job.Wait()
	}()

	if job == nil {
		return TestResult{Success: false, Error: "Wait failed or returned nil"}
	}

	// Check if connection was successful
	var success bool
	func() {
		defer func() {
			if r := recover(); r != nil {
				success = false
			}
		}()
		success = job.Success()
	}()

	if !success {
		return TestResult{Success: false, Error: "Connection test failed - controller could not connect to window"}
	}

	return TestResult{
		Success:     true,
		WindowTitle: targetWindow.Title,
		Error:       "",
	}
}

// SaveWin32Config saves the Win32 configuration to a file
func (a *App) SaveWin32Config(config Win32Config) error {
	exePath, err := os.Executable()
	if err != nil {
		return fmt.Errorf("failed to get executable path: %v", err)
	}
	exeDir := filepath.Dir(exePath)
	configDir := filepath.Join(exeDir, "config")

	// Create config directory if it doesn't exist
	if err := os.MkdirAll(configDir, 0755); err != nil {
		return fmt.Errorf("failed to create config directory: %v", err)
	}

	configPath := filepath.Join(configDir, "win32_config.json")

	data, err := json.MarshalIndent(config, "", "  ")
	if err != nil {
		return fmt.Errorf("failed to marshal config: %v", err)
	}

	if err := os.WriteFile(configPath, data, 0644); err != nil {
		return fmt.Errorf("failed to write config file: %v", err)
	}

	return nil
}

// LoadWin32Config loads the Win32 configuration from a file
func (a *App) LoadWin32Config() (*Win32Config, error) {
	exePath, err := os.Executable()
	if err != nil {
		return nil, fmt.Errorf("failed to get executable path: %v", err)
	}
	exeDir := filepath.Dir(exePath)
	configPath := filepath.Join(exeDir, "config", "win32_config.json")

	data, err := os.ReadFile(configPath)
	if err != nil {
		// Return default config if file doesn't exist
		if os.IsNotExist(err) {
			return &Win32Config{
				ClassRegex:  "",
				WindowRegex: "",
				Screencap:   "FramePool",
				Mouse:       "PostMessage",
				Keyboard:    "PostMessage",
			}, nil
		}
		return nil, fmt.Errorf("failed to read config file: %v", err)
	}

	var config Win32Config
	if err := json.Unmarshal(data, &config); err != nil {
		return nil, fmt.Errorf("failed to unmarshal config: %v", err)
	}

	return &config, nil
}

// ScanAllWindows scans and returns all visible windows
func (a *App) ScanAllWindows() []WindowInfo {
	return findAllWindows()
}

// Helper functions

func parseScreencapMethod(method string) win32.ScreencapMethod {
	switch method {
	case "GDI":
		return 1
	case "FramePool":
		return 2
	case "DXGI_DesktopDup":
		return 4
	case "DXGI_DesktopDup_Window":
		return 8
	case "PrintWindow":
		return 16
	case "ScreenDC":
		return 32
	default:
		return 2 // Default to FramePool
	}
}

func parseInputMethod(method string) win32.InputMethod {
	switch method {
	case "Seize":
		return 1
	case "SendMessage":
		return 2
	case "PostMessage":
		return 4
	case "LegacyEvent":
		return 8
	case "PostThreadMessage":
		return 16
	case "SendMessageWithCursorPos":
		return 32
	case "PostMessageWithCursorPos":
		return 64
	default:
		return 4 // Default to PostMessage
	}
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
