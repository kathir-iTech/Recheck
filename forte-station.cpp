#include <array>
#include <cerrno>
#include <cstdint>
#include <cstdio>
#include <cstring>
#include <fcntl.h>
#include <termios.h>
#include <unistd.h>

#include "forte/datatypes/forte_uint.h"
#include "forte/funcbloc.h"
#include "forte/typelib.h"

using namespace forte::literals;

namespace forte {
namespace {
// FORTE matches port IDs by pointer identity. Use its shared intern table across modules.
const auto eventNames = std::array{StringId::insert("REQ")};
const auto inputNames = std::array{StringId::insert("COUNT"), StringId::insert("COMMAND"),
                                   StringId::insert("REGISTER")};
const SFBInterfaceSpec interfaceSpec = {
    .mEINames = eventNames, .mEITypeNames = {}, .mEONames = {}, .mEOTypeNames = {},
    .mDINames = inputNames, .mDONames = {}, .mDIONames = {},
    .mSocketNames = {}, .mPlugNames = {},
};

bool transfer(int fd, uint8_t *data, size_t length, bool sending) {
    while (length > 0) {
        ssize_t done = sending ? write(fd, data, length) : read(fd, data, length);
        if (done < 0 && errno == EINTR) continue;
        if (done <= 0) return false;
        data += done;
        length -= static_cast<size_t>(done);
    }
    return true;
}

bool advance_station(uint16_t command, uint16_t address) {
    int fd = open("/dev/ser1", O_RDWR | O_NOCTTY);
    if (fd < 0) return false;
    termios settings{};
    bool success = false;
    if (tcgetattr(fd, &settings) == 0) {
        cfmakeraw(&settings);
        settings.c_cc[VMIN] = 0;
        // The host may replace the station before forwarding this reply.
        settings.c_cc[VTIME] = 250;
        if (tcsetattr(fd, TCSANOW, &settings) == 0) {
            uint8_t request[] = {0, 1, 0, 0, 0, 11, 1, 16, 0, uint8_t(address), 0, 2, 4,
                                 uint8_t(command >> 8), uint8_t(command), 0, 1};
            uint8_t expected[] = {0, 1, 0, 0, 0, 6, 1, 16, 0, uint8_t(address), 0, 2};
            uint8_t reply[sizeof(expected)]{};
            success = transfer(fd, request, sizeof(request), true) &&
                      transfer(fd, reply, sizeof(reply), false) &&
                      memcmp(reply, expected, sizeof(reply)) == 0;
        }
    }
    close(fd);
    return success;
}
}

class RecheckStation final : public CFunctionBlock {
    DECLARE_FIRMWARE_FB(RecheckStation)

public:
    RecheckStation(StringId name, CFBContainer &container) : CFunctionBlock(container, interfaceSpec, name) {}
    CIEC_ANY *getDI(size_t index) override { return index < values.size() ? &values[index] : nullptr; }
    CIEC_ANY *getDO(size_t) override { return nullptr; }

private:
    std::array<CIEC_UINT, 3> values{};
    std::array<CDataConnection *, 3> connections{};

    void setInitialValues() override { values.fill(CIEC_UINT(0)); }
    CEventConnection *getEOConUnchecked(TPortId) override { return nullptr; }
    CDataConnection *getDOConUnchecked(TPortId) override { return nullptr; }
    CDataConnection **getDIConUnchecked(TPortId index) override { return &connections[index]; }
    void writeOutputData(TEventID) override {}
    void readInputData(TEventID) override {
        for (size_t index = 0; index < values.size(); ++index) {
            readData(index, values[index], connections[index]);
        }
    }
    void executeEvent(TEventID event, CEventChainExecutionThread *) override {
        if (event != 0 || static_cast<TForteUInt16>(values[0]) != 2) return;
        uint16_t command = static_cast<TForteUInt16>(values[1]);
        uint16_t address = static_cast<TForteUInt16>(values[2]);
        if (command != 0 && (address == 8 || address == 10) && advance_station(command, address)) {
            std::printf("FORTE_EFFECT_ACK pid=%d command=%u counter=2 register=%u\n", getpid(),
                        static_cast<unsigned>(command), static_cast<unsigned>(address));
            std::fflush(stdout);
            _exit(86);
        }
        std::fputs("FORTE_EFFECT_INCOMPLETE\n", stderr);
        _exit(1);
    }
};

DEFINE_FIRMWARE_FB(RecheckStation, "recheck::Station"_STRID)
}
