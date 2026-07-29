CC ?= cc
CFLAGS ?= -O2 -std=c11 -Wall -Wextra -Wpedantic -pthread
CPPFLAGS ?= -Iinclude
LDFLAGS ?= -pthread -lm

FFMPEG_INCLUDE ?= ffmpeg_sdk/include
FFMPEG_LIB_DIR ?= ../lib
SOUNDSOLUTION_LIB_DIR ?= ../bin/soundsolution
SOUNDSOLUTION_LIBS := -l:libsoundsolution.so.2

FFMPEG_LIBS := \
	-l:libavformat.so.61 \
	-l:libavcodec.so.61 \
	-l:libswresample.so.5 \
	-l:libavutil.so.59

CPPFLAGS += -I$(FFMPEG_INCLUDE)
LDFLAGS += -L$(FFMPEG_LIB_DIR) $(FFMPEG_LIBS) \
	-L$(SOUNDSOLUTION_LIB_DIR) $(SOUNDSOLUTION_LIBS) \
	-Wl,--disable-new-dtags,-rpath,'$$ORIGIN/../../lib:$$ORIGIN/../../bin/soundsolution'

TARGET := bin/web_broadcaster_engine
SOURCES := src/main.c src/daemon.c src/engine.c src/protocol.c src/audio_probe.c src/audio_analysis.c src/native_timing.c src/icecast_output.c src/diagnostics.c src/libav_bridge.c
OBJECTS := $(SOURCES:.c=.o)

.PHONY: all clean run

all: $(TARGET)

$(TARGET): $(OBJECTS) | bin
	$(CC) $(OBJECTS) $(LDFLAGS) -o $@

src/%.o: src/%.c include/engine.h include/daemon.h include/protocol.h include/audio_probe.h include/audio_analysis.h include/native_timing.h include/icecast_output.h include/diagnostics.h include/libav_bridge.h
	$(CC) $(CPPFLAGS) $(CFLAGS) -c $< -o $@

bin:
	mkdir -p bin

run: $(TARGET)
	./$(TARGET)

clean:
	rm -f $(OBJECTS) $(TARGET)
