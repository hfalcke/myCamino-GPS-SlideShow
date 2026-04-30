CXX ?= clang++
SDKROOT ?= $(shell xcrun --show-sdk-path)
LIBCXX_HEADERS ?= $(SDKROOT)/usr/include/c++/v1
APP_NAME := myCaminoGPXEditor
SRC := cpp/myCaminoGPXEditor.mm
BUILD_DIR := build
BIN := $(BUILD_DIR)/$(APP_NAME)

CXXFLAGS := -std=c++20 -fobjc-arc -isysroot $(SDKROOT) -isystem $(LIBCXX_HEADERS) -Wall -Wextra -Wno-unused-parameter -Wno-deprecated-declarations
FRAMEWORKS := -framework AppKit -framework Foundation -framework QuartzCore

.PHONY: all clean run

all: $(BIN)

$(BIN): $(SRC) | $(BUILD_DIR)
	$(CXX) $(CXXFLAGS) $(SRC) $(FRAMEWORKS) -o $(BIN)

$(BUILD_DIR):
	mkdir -p $(BUILD_DIR)

run: $(BIN)
	./$(BIN)

clean:
	rm -rf $(BUILD_DIR)
