#include <arpa/inet.h>
#include <errno.h>
#include <fcntl.h>
#include <stdint.h>
#include <signal.h>
#include <stdio.h>
#include <string.h>
#include <sys/socket.h>
#include <sys/time.h>
#include <sys/utsname.h>
#include <termios.h>
#include <unistd.h>

static int exchange_all(int fd, uint8_t *data, size_t length, int sending) {
    while (length) {
#ifdef STATION_SERIAL
        ssize_t result = sending ? write(fd, data, length) : read(fd, data, length);
#else
        ssize_t result = sending ? send(fd, data, length, 0) : recv(fd, data, length, 0);
#endif
        if (result < 0 && errno == EINTR) continue;
        if (result <= 0) return -1;
        data += result;
        length -= (size_t)result;
    }
    return 0;
}

static int run_probe(int fd) {
#ifndef STATION_SERIAL
    struct timeval timeout = {.tv_sec = 4};
    if (setsockopt(fd, SOL_SOCKET, SO_SNDTIMEO, &timeout, sizeof(timeout)) ||
        setsockopt(fd, SOL_SOCKET, SO_RCVTIMEO, &timeout, sizeof(timeout))) {
        perror("socket timeout");
        return 1;
    }
    struct sockaddr_in station = {.sin_family = AF_INET, .sin_port = htons(1502)};
    if (inet_pton(AF_INET, "10.0.2.100", &station.sin_addr) != 1 ||
        connect(fd, (const struct sockaddr *)&station, sizeof(station)) != 0) {
        perror("connect to packaging station");
        return 1;
    }
#endif

    /* Modbus/TCP transaction 1, unit 1, write single holding register 0 = 41. */
    uint8_t request[] = {0, 1, 0, 0, 0, 6, 1, 6, 0, 0, 0, 41};
    uint8_t reply[sizeof(request)];
    if (exchange_all(fd, request, sizeof(request), 1) ||
        exchange_all(fd, reply, 7, 0) ||
        memcmp(reply, request, 7) != 0 ||
        exchange_all(fd, reply + 7, sizeof(reply) - 7, 0) ||
        memcmp(reply + 7, request + 7, sizeof(reply) - 7) != 0) {
        fputs("Invalid or incomplete Modbus write acknowledgement\n", stderr);
        return 1;
    }
    printf("MODBUS_WRITE_OK {\"os\":\"QNX\",\"pid\":%d,\"command_id\":41,\"transaction_id\":1}\n",
           getpid());
    return 0;
}

int main(void) {
    struct utsname system;
    if (uname(&system) != 0 || strcmp(system.sysname, "QNX") != 0) return 1;
    if (signal(SIGPIPE, SIG_IGN) == SIG_ERR) {
        perror("ignore SIGPIPE");
        return 1;
    }
#ifdef STATION_SERIAL
    int fd = open("/dev/ser1", O_RDWR | O_NOCTTY);
    if (fd >= 0) {
        struct termios settings;
        if (tcgetattr(fd, &settings) == 0) {
            cfmakeraw(&settings);
            settings.c_cc[VMIN] = 0;
            settings.c_cc[VTIME] = 40;
            if (tcsetattr(fd, TCSANOW, &settings) == 0) {
                int result = run_probe(fd);
                close(fd);
                return result;
            }
        }
        close(fd);
    }
    perror("station serial link");
    return 1;
#else
    int fd = socket(AF_INET, SOCK_STREAM, 0);
    if (fd < 0) {
        perror("station socket");
        return 1;
    }
    int result = run_probe(fd);
    close(fd);
    return result;
#endif
}
