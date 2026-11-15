package main

import (
	"encoding/json"
	"errors"
	"io"
	"net/http"
	"regexp"
	"strings"

	"github.com/mattermost/mattermost/server/public/model"
	"github.com/mattermost/mattermost/server/public/plugin"
)

const (
	maxKeyboardBytes = 64 * 1024
	websocketEvent   = "keyboard_updated"
)

var (
	channelPath = regexp.MustCompile(`^/api/v1/channels/([A-Za-z0-9]+)/keyboard$`)
	buttonID    = regexp.MustCompile(`^[A-Za-z0-9_-]{1,64}$`)
)

type Plugin struct {
	plugin.MattermostPlugin
}

type composerButton struct {
	ID       string `json:"id"`
	Text     string `json:"text"`
	Message  string `json:"message"`
	Style    string `json:"style"`
	Disabled bool   `json:"disabled"`
}

type composerKeyboard struct {
	SchemaVersion int                `json:"schema_version"`
	Title         string             `json:"title"`
	Rows          [][]composerButton `json:"rows"`
}

type keyboardState struct {
	ChannelID string           `json:"channel_id"`
	Revision  int64            `json:"revision"`
	Keyboard  composerKeyboard `json:"keyboard"`
}

func keyboardKey(channelID string) string {
	return "keyboard:" + channelID
}

func cleanText(value string, maximum int) bool {
	return value != "" && strings.TrimSpace(value) == value && len([]byte(value)) <= maximum &&
		!strings.ContainsRune(value, '\x00')
}

func singleLine(value string, maximum int) bool {
	if !cleanText(value, maximum) {
		return false
	}
	for _, character := range value {
		if character < 32 || character == 127 {
			return false
		}
	}
	return true
}

func validateKeyboard(value *composerKeyboard) error {
	if value.SchemaVersion != 1 {
		return errors.New("schema_version must be 1")
	}
	if !singleLine(value.Title, 120) {
		return errors.New("title must be trimmed single-line text of at most 120 bytes")
	}
	if len(value.Rows) < 1 || len(value.Rows) > 6 {
		return errors.New("keyboard requires 1-6 rows")
	}
	seen := map[string]bool{}
	for _, row := range value.Rows {
		if len(row) < 1 || len(row) > 4 {
			return errors.New("each row requires 1-4 buttons")
		}
		for _, button := range row {
			if !buttonID.MatchString(button.ID) || seen[button.ID] {
				return errors.New("button IDs must be unique 1-64 character identifiers")
			}
			seen[button.ID] = true
			if !singleLine(button.Text, 120) {
				return errors.New("button text must be trimmed single-line text of at most 120 bytes")
			}
			if !cleanText(button.Message, 4000) {
				return errors.New("button message must be trimmed text of at most 4000 bytes")
			}
			if button.Style != "default" && button.Style != "primary" && button.Style != "danger" {
				return errors.New("button style must be default, primary or danger")
			}
		}
	}
	return nil
}

func (p *Plugin) ServeHTTP(_ *plugin.Context, w http.ResponseWriter, r *http.Request) {
	w.Header().Set("Content-Type", "application/json")
	w.Header().Set("Cache-Control", "no-store")
	match := channelPath.FindStringSubmatch(r.URL.Path)
	if match == nil {
		writeError(w, http.StatusNotFound, "route_not_found", "Route not found")
		return
	}
	userID := r.Header.Get("Mattermost-User-ID")
	if userID == "" {
		writeError(w, http.StatusUnauthorized, "authentication_required", "Authentication required")
		return
	}
	channelID := match[1]
	if _, appErr := p.API.GetChannelMember(channelID, userID); appErr != nil {
		writeError(w, http.StatusForbidden, "channel_membership_required", "Channel membership required")
		return
	}

	switch r.Method {
	case http.MethodGet:
		p.getKeyboard(w, channelID)
	case http.MethodPut:
		if !p.canManage(userID) {
			writeError(w, http.StatusForbidden, "keyboard_manager_required", "Bot or system administrator required")
			return
		}
		p.putKeyboard(w, r, channelID)
	case http.MethodDelete:
		if !p.canManage(userID) {
			writeError(w, http.StatusForbidden, "keyboard_manager_required", "Bot or system administrator required")
			return
		}
		p.deleteKeyboard(w, channelID)
	default:
		w.Header().Set("Allow", "GET, PUT, DELETE")
		writeError(w, http.StatusMethodNotAllowed, "method_not_allowed", "Method not allowed")
	}
}

func (p *Plugin) canManage(userID string) bool {
	user, appErr := p.API.GetUser(userID)
	if appErr != nil {
		return false
	}
	return user.IsBot || strings.Contains(" "+user.Roles+" ", " system_admin ")
}

func (p *Plugin) getKeyboard(w http.ResponseWriter, channelID string) {
	raw, appErr := p.API.KVGet(keyboardKey(channelID))
	if appErr != nil {
		writeError(w, http.StatusInternalServerError, "keyboard_read_failed", "Keyboard read failed")
		return
	}
	if raw == nil {
		writeJSON(w, http.StatusOK, map[string]any{"keyboard": nil})
		return
	}
	var state keyboardState
	if err := json.Unmarshal(raw, &state); err != nil {
		writeError(w, http.StatusInternalServerError, "keyboard_state_invalid", "Stored keyboard state is invalid")
		return
	}
	writeJSON(w, http.StatusOK, state)
}

func decodeKeyboard(r *http.Request) (composerKeyboard, error) {
	var keyboard composerKeyboard
	decoder := json.NewDecoder(io.LimitReader(r.Body, maxKeyboardBytes+1))
	decoder.DisallowUnknownFields()
	if err := decoder.Decode(&keyboard); err != nil {
		return keyboard, err
	}
	if decoder.Decode(&struct{}{}) != io.EOF {
		return keyboard, errors.New("request must contain exactly one JSON object")
	}
	return keyboard, validateKeyboard(&keyboard)
}

func (p *Plugin) putKeyboard(w http.ResponseWriter, r *http.Request, channelID string) {
	keyboard, err := decodeKeyboard(r)
	if err != nil {
		writeError(w, http.StatusBadRequest, "invalid_keyboard", err.Error())
		return
	}
	key := keyboardKey(channelID)
	for attempt := 0; attempt < 8; attempt++ {
		previous, appErr := p.API.KVGet(key)
		if appErr != nil {
			writeError(w, http.StatusInternalServerError, "keyboard_read_failed", "Keyboard read failed")
			return
		}
		revision := int64(1)
		if previous != nil {
			var current keyboardState
			if json.Unmarshal(previous, &current) != nil {
				writeError(w, http.StatusInternalServerError, "keyboard_state_invalid", "Stored keyboard state is invalid")
				return
			}
			revision = current.Revision + 1
		}
		state := keyboardState{ChannelID: channelID, Revision: revision, Keyboard: keyboard}
		next, marshalErr := json.Marshal(state)
		if marshalErr != nil {
			writeError(w, http.StatusInternalServerError, "keyboard_encode_failed", "Keyboard encoding failed")
			return
		}
		swapped, appErr := p.API.KVCompareAndSet(key, previous, next)
		if appErr != nil {
			writeError(w, http.StatusInternalServerError, "keyboard_write_failed", "Keyboard write failed")
			return
		}
		if swapped {
			p.publish(channelID, revision, false)
			writeJSON(w, http.StatusOK, state)
			return
		}
	}
	writeError(w, http.StatusConflict, "keyboard_write_conflict", "Keyboard changed concurrently; retry after reading it")
}

func (p *Plugin) deleteKeyboard(w http.ResponseWriter, channelID string) {
	key := keyboardKey(channelID)
	for attempt := 0; attempt < 8; attempt++ {
		previous, appErr := p.API.KVGet(key)
		if appErr != nil {
			writeError(w, http.StatusInternalServerError, "keyboard_read_failed", "Keyboard read failed")
			return
		}
		if previous == nil {
			writeJSON(w, http.StatusOK, map[string]string{"status": "OK"})
			return
		}
		deleted, appErr := p.API.KVCompareAndDelete(key, previous)
		if appErr != nil {
			writeError(w, http.StatusInternalServerError, "keyboard_delete_failed", "Keyboard deletion failed")
			return
		}
		if deleted {
			p.publish(channelID, 0, true)
			writeJSON(w, http.StatusOK, map[string]string{"status": "OK"})
			return
		}
	}
	writeError(w, http.StatusConflict, "keyboard_delete_conflict", "Keyboard changed concurrently; retry after reading it")
}

func (p *Plugin) publish(channelID string, revision int64, deleted bool) {
	p.API.PublishWebSocketEvent(websocketEvent, map[string]any{
		"channel_id": channelID,
		"revision":   revision,
		"deleted":    deleted,
	}, &model.WebsocketBroadcast{ChannelId: channelID})
}

func writeJSON(w http.ResponseWriter, status int, value any) {
	w.WriteHeader(status)
	_ = json.NewEncoder(w).Encode(value)
}

func writeError(w http.ResponseWriter, status int, id, message string) {
	writeJSON(w, status, map[string]string{"id": "com.mattergram.menu." + id, "message": message})
}
