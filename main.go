package main

import (
	"arknights-auto-operator/backend/appconf"
	"arknights-auto-operator/backend/engine"
	"arknights-auto-operator/backend/fileloader"
	"arknights-auto-operator/backend/system"
	"arknights-auto-operator/backend/task"
	"context"
	"embed"
	"net/http"
	"os"
	"path/filepath"

	"github.com/wailsapp/wails/v2"
	"github.com/wailsapp/wails/v2/pkg/options"
	"github.com/wailsapp/wails/v2/pkg/options/assetserver"
)

//go:embed all:frontend/dist
var assets embed.FS

func main() {
	// Initialize services
	taskSrv := task.Task()
	appConfSrv := appconf.AppConf()
	engSrv := engine.Engine()
	sysSrv := system.System()

	// Get executable directory
	exePath, err := os.Executable()
	if err != nil {
		panic(err)
	}
	exeDir := filepath.Dir(exePath)

	// Setup HTTP multiplexer for static files
	mux := http.NewServeMux()
	assetsDir := filepath.Join(exeDir, "static")
	assetsLoader := fileloader.New(assetsDir)
	mux.Handle("/static/", http.StripPrefix("/static/", assetsLoader))
	resDir := filepath.Join(exeDir, "resource")
	resLoader := fileloader.New(resDir)
	mux.Handle("/resource/", http.StripPrefix("/resource/", resLoader))

	// Run Wails application
	err = wails.Run(&options.App{
		Title:  "Arknights Auto Operator",
		Width:  1024,
		Height: 768,
		AssetServer: &assetserver.Options{
			Assets:  assets,
			Handler: mux,
		},
		Frameless:        true,
		BackgroundColour: &options.RGBA{R: 27, G: 38, B: 54, A: 1},
		OnStartup: func(ctx context.Context) {
			task.Startup(ctx)
			appconf.Startup(ctx)
			engine.Startup(ctx)
			system.Startup(ctx)
		},
		Bind: []interface{}{
			taskSrv,
			appConfSrv,
			engSrv,
			sysSrv,
		},
	})

	if err != nil {
		println("Error:", err.Error())
	}
}
