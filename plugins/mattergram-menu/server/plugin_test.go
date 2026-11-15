package main

import "testing"

func validKeyboard() composerKeyboard {
	return composerKeyboard{
		SchemaVersion: 1,
		Title:         "Actions",
		Rows: [][]composerButton{{
			{ID: "accounts", Text: "Accounts", Message: "/accounts", Style: "primary"},
			{ID: "imports", Text: "Imports", Message: "/imports", Style: "default"},
		}},
	}
}

func TestValidateKeyboard(t *testing.T) {
	value := validKeyboard()
	if err := validateKeyboard(&value); err != nil {
		t.Fatalf("valid keyboard rejected: %v", err)
	}

	value = validKeyboard()
	value.Rows[0][1].ID = "accounts"
	if err := validateKeyboard(&value); err == nil {
		t.Fatal("duplicate button ID accepted")
	}

	value = validKeyboard()
	value.Rows[0][0].Message = ""
	if err := validateKeyboard(&value); err == nil {
		t.Fatal("empty message accepted")
	}

	value = validKeyboard()
	value.Rows = append(value.Rows, []composerButton{}, []composerButton{}, []composerButton{}, []composerButton{}, []composerButton{}, []composerButton{})
	if err := validateKeyboard(&value); err == nil {
		t.Fatal("oversized layout accepted")
	}
}
