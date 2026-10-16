//go:build js && wasm

// Command wasm is r6-dissect for web browsers, used by the browser version of R6 Match Stats
// (browser/index.html). Build it with:
//
//	GOOS=js GOARCH=wasm go build -o browser/r6-dissect.wasm ./wasm
//
// It gives the page r6dissect.parseMatch(rounds): rounds is an array of Uint8Arrays, one .rec
// file per round in play order. It returns {json, warnings}: json is what the CLI writes for a
// match folder, and warnings name the rounds that couldn't be read (they're skipped, so one
// broken file doesn't lose the match). It returns {error} when no round could be read.
// Nothing leaves the browser: the files are read in the page's memory.
package main

import (
	"bytes"
	"encoding/json"
	"fmt"
	"runtime"
	"runtime/debug"
	"syscall/js"

	"github.com/redraskal/r6-dissect/dissect"
	"github.com/rs/zerolog"
)

func parseMatch(_ js.Value, args []js.Value) (result any) {
	defer func() {
		if r := recover(); r != nil {
			result = map[string]any{"error": fmt.Sprint("r6-dissect stopped: ", r)}
		}
	}()
	if len(args) != 1 || args[0].Type() != js.TypeObject {
		return map[string]any{"error": "parseMatch takes an array of Uint8Arrays, one per round"}
	}
	files := args[0]
	var rounds []*dissect.Reader
	warnings := []any{}
	for i := 0; i < files.Length(); i++ {
		data := make([]byte, files.Index(i).Get("length").Int())
		js.CopyBytesToGo(data, files.Index(i))
		r, err := dissect.NewReader(bytes.NewReader(data))
		if err == nil {
			err = r.Read()
		}
		if !dissect.Ok(err) {
			warnings = append(warnings, fmt.Sprintf("Skipped round %d: %v", i+1, err))
		} else {
			rounds = append(rounds, r)
		}
		data = nil
		runtime.GC() // each round is decompressed into a large buffer that's garbage once read
	}
	if len(rounds) == 0 {
		return map[string]any{"error": "No round could be read.", "warnings": warnings}
	}
	out, err := json.Marshal(dissect.NewMatchReaderFromRounds(rounds).Data())
	if err != nil {
		return map[string]any{"error": err.Error(), "warnings": warnings}
	}
	return map[string]any{"json": string(out), "warnings": warnings}
}

func main() {
	debug.SetGCPercent(50) // as the CLI: roughly halves peak memory
	zerolog.SetGlobalLevel(zerolog.WarnLevel)
	js.Global().Set("r6dissect", js.ValueOf(map[string]any{
		"parseMatch": js.FuncOf(parseMatch),
		"ready":      true,
	}))
	select {} // keep the functions available to the page
}
