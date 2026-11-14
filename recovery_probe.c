#include <errno.h>
#include <fcntl.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/neutrino.h>
#include <sys/utsname.h>
#include <sys/wait.h>
#include <termios.h>
#include <time.h>
#include <unistd.h>

enum checkpoint { NO_CRASH, BEFORE_ACTION, AFTER_ACTION, AFTER_REPLY };
enum station_register { EFFECT_COUNT = 1, BLIND_INPUT = 8, PROTECTED_INPUT = 10 };
enum { INCOMPLETE = 1, CONFLICT = 2, EXIT_AT_CHECKPOINT = 86 };

struct command { uint16_t id, amount, address; };
struct controller { pid_t pid; int connection; };
static int station_fd;

static void check(int success, const char *reason) {
    if (success) return;
    fprintf(stderr, "RECOVERY_INCOMPLETE %s (errno=%d)\n", reason, errno);
    exit(INCOMPLETE);
}

static void station_exchange(uint8_t *request, size_t size, uint8_t *response, size_t capacity) {
    size_t sent = 0, received = 0, needed = 7;
    while (sent < size) {
        ssize_t result = write(station_fd, request + sent, size - sent);
        if (result < 0 && errno == EINTR) continue;
        check(result > 0, "station request write");
        sent += (size_t)result;
    }
    while (received < needed) {
        ssize_t result = read(station_fd, response + received, needed - received);
        if (result < 0 && errno == EINTR) continue;
        check(result > 0, "station response unavailable");
        received += (size_t)result;
        if (received == 7) {
            check(memcmp(request, response, 4) == 0 && response[6] == 1, "station response header");
            size_t length = ((size_t)response[4] << 8) | response[5];
            check(length >= 2 && length <= capacity - 6, "station response length");
            needed = length + 6;
        }
    }
}

static int observe(void) {
    uint8_t request[] = {0, 2, 0, 0, 0, 6, 1, 3, 0, EFFECT_COUNT, 0, 1};
    uint8_t response[12] = {0};
    station_exchange(request, sizeof(request), response, sizeof(response));
    check(response[5] == 5 && response[7] == 3 && response[8] == 2, "observer could not establish effect count");
    return (response[9] << 8) | response[10];
}

static int apply(const struct command *command) {
    uint8_t request[] = {0, 1, 0, 0, 0, 11, 1, 16, 0, (uint8_t)command->address, 0, 2, 4,
                         (uint8_t)(command->id >> 8), (uint8_t)command->id,
                         (uint8_t)(command->amount >> 8), (uint8_t)command->amount};
    uint8_t response[12] = {0};
    station_exchange(request, sizeof(request), response, sizeof(response));
    if (response[5] == 3 && response[7] == 0x90 && response[8] == 3) return CONFLICT;
    check(response[5] == 6 && memcmp(response + 7, request + 7, 5) == 0, "station rejected command");
    return EOK;
}

static void set_timeout(unsigned flags) {
    uint64_t timeout = 10000000000ULL;
    check(TimerTimeout(CLOCK_MONOTONIC, flags, NULL, &timeout, NULL) == 0, "IPC timeout setup");
}

static struct controller start_controller(enum checkpoint stop) {
    int ready = ChannelCreate(0);
    check(ready >= 0, "handshake channel");
    pid_t supervisor = getpid();
    pid_t pid = fork();
    check(pid >= 0, "fork controller");
    if (pid == 0) {
        int channel = ChannelCreate(0);
        int parent = ConnectAttach(0, supervisor, ready, _NTO_SIDE_CHANNEL, 0);
        check(channel >= 0 && parent >= 0, "controller initialization");
        set_timeout(_NTO_TIMEOUT_SEND | _NTO_TIMEOUT_REPLY);
        check(MsgSend(parent, &channel, sizeof(channel), NULL, 0) == EOK, "controller readiness");
        ConnectDetach(parent);
        struct command command;
        struct _msg_info info;
        set_timeout(_NTO_TIMEOUT_RECEIVE);
        int received = MsgReceive(channel, &command, sizeof(command), &info);
        check(received > 0 && info.srcmsglen == sizeof(command), "controller receive");
        if (stop == BEFORE_ACTION) _exit(EXIT_AT_CHECKPOINT);
        int outcome = apply(&command);
        if (stop == AFTER_ACTION) {
            check(outcome == EOK, "effect checkpoint requires station acknowledgement");
            _exit(EXIT_AT_CHECKPOINT);
        }
        check(MsgReply(received, outcome, NULL, 0) == EOK, "controller reply");
        _exit(stop == AFTER_REPLY ? EXIT_AT_CHECKPOINT : 0);
    }
    int channel;
    struct _msg_info info;
    set_timeout(_NTO_TIMEOUT_RECEIVE);
    int received = MsgReceive(ready, &channel, sizeof(channel), &info);
    check(received > 0 && info.pid == pid && info.srcmsglen == sizeof(channel), "controller handshake");
    int connection = ConnectAttach(0, pid, channel, _NTO_SIDE_CHANNEL, 0);
    check(connection >= 0 && MsgReply(received, EOK, NULL, 0) == EOK, "connect to controller");
    ChannelDestroy(ready);
    return (struct controller){pid, connection};
}

static pid_t deliver(struct command command, enum checkpoint stop, int expected) {
    struct controller controller = start_controller(stop);
    set_timeout(_NTO_TIMEOUT_SEND | _NTO_TIMEOUT_REPLY);
    int result = MsgSend(controller.connection, &command, sizeof(command), NULL, 0);
    int sender_error = errno;
    ConnectDetach(controller.connection);
    int status;
    check(waitpid(controller.pid, &status, 0) == controller.pid && WIFEXITED(status) &&
          WEXITSTATUS(status) == (stop == NO_CRASH ? 0 : EXIT_AT_CHECKPOINT), "controller exit status");
    if (stop == BEFORE_ACTION || stop == AFTER_ACTION) {
        check(result == -1 && sender_error == ESRCH, "expected QNX ESRCH from missing reply");
    } else {
        check(result == expected, "unexpected controller acknowledgement");
    }
    return controller.pid;
}

static void run_case(const char *name, struct command command, enum checkpoint stop, int retry) {
    int baseline = observe();
    pid_t original = deliver(command, stop, EOK);
    int before_retry = observe();
    pid_t replacement = retry ? deliver(command, NO_CRASH, EOK) : 0;
    int final = observe();
    if (retry) check(original != replacement, "controller was not replaced");
    const char *reply = (stop == BEFORE_ACTION || stop == AFTER_ACTION) ? "ESRCH" : "ACK";
    printf("RECOVERY_CASE {\"case\":\"%s\",\"command_id\":%u,\"caller_reply\":\"%s\","
           "\"original_pid\":%d,\"replacement_pid\":%d,\"baseline\":%d,"
           "\"before_retry\":%d,\"final\":%d}\n",
           name, command.id, reply, original, replacement, baseline, before_retry, final);
    fflush(stdout);
}

static void run_history_cases(void) {
    struct command first = {51, 1, PROTECTED_INPUT};
    struct command next = {52, 1, PROTECTED_INPUT};
    int baseline = observe();
    deliver(first, NO_CRASH, EOK);
    deliver(next, NO_CRASH, EOK);
    int before_retry = observe();
    deliver(first, NO_CRASH, EOK);
    int after_retry = observe();
    first.amount = 2;
    deliver(first, NO_CRASH, CONFLICT);
    int after_conflict = observe();
    printf("RECOVERY_HISTORY {\"baseline\":%d,\"before_retry\":%d,\"after_retry\":%d,"
           "\"after_conflict\":%d,\"conflict_acknowledged\":true}\n",
           baseline, before_retry, after_retry, after_conflict);
    fflush(stdout);
}

int main(void) {
    struct utsname system;
    check(uname(&system) == 0 && strcmp(system.sysname, "QNX") == 0, "QNX guest required");
    station_fd = open("/dev/ser1", O_RDWR | O_NOCTTY);
    check(station_fd >= 0, "open station serial link");
    struct termios settings;
    check(tcgetattr(station_fd, &settings) == 0, "read serial settings");
    cfmakeraw(&settings);
    settings.c_cc[VMIN] = 0;
    settings.c_cc[VTIME] = 40;
    check(tcsetattr(station_fd, TCSANOW, &settings) == 0, "configure station serial link");

    run_case("normal", (struct command){41, 1, BLIND_INPUT}, NO_CRASH, 0);
    run_case("before_action_retry", (struct command){42, 1, PROTECTED_INPUT}, BEFORE_ACTION, 1);
    run_case("before_action_suppressed", (struct command){43, 1, BLIND_INPUT}, BEFORE_ACTION, 0);
    run_case("after_action_blind_retry", (struct command){44, 1, BLIND_INPUT}, AFTER_ACTION, 1);
    run_case("after_action_protected_retry", (struct command){45, 1, PROTECTED_INPUT}, AFTER_ACTION, 1);
    run_case("after_reply_control", (struct command){46, 1, BLIND_INPUT}, AFTER_REPLY, 0);
    run_history_cases();
    puts("RECOVERY_SUITE_OK");
    close(station_fd);
    return 0;
}
