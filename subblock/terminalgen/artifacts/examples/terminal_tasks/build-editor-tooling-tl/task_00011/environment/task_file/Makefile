CXX = g++
CXXFLAGS = -std=c++11 -fopenmp -Wall
LDFLAGS = 
LDLIBS = 

SRC_DIR = src
BUILD_DIR = build
OUTPUT_DIR = output

SOURCES = $(SRC_DIR)/main.cpp $(SRC_DIR)/worker.cpp
OBJECTS = $(BUILD_DIR)/main.o $(BUILD_DIR)/worker.o
TARGET = $(OUTPUT_DIR)/worker_app

all: $(TARGET)

$(BUILD_DIR):
	mkdir -p $(BUILD_DIR)

$(OUTPUT_DIR):
	mkdir -p $(OUTPUT_DIR)

$(BUILD_DIR)/%.o: $(SRC_DIR)/%.cpp | $(BUILD_DIR)
	$(CXX) $(CXXFLAGS) -c $< -o $@

$(TARGET): $(OBJECTS) | $(OUTPUT_DIR)
	$(CXX) $(LDFLAGS) -o $@ $(OBJECTS) $(LDLIBS)

clean:
	rm -rf $(BUILD_DIR) $(OUTPUT_DIR)

.PHONY: all clean
