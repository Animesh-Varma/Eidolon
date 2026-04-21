#include <stdint.h>
#include <string.h>
#include "esp_log.h"
#include "esp_bt_main.h"
#include "esp_bt_device.h"
#include "esp_gap_bt_api.h"
#include "esp_hf_client_api.h"
#include "freertos/FreeRTOS.h"
#include "freertos/ringbuf.h"
#include "lwip/sockets.h"

#define BT_HF_TAG "BT_HF"

extern int sock_audio;
extern int sock_ctrl;
extern volatile bool is_tcp_connected;

RingbufHandle_t rb_to_wifi = NULL;
RingbufHandle_t rb_from_wifi = NULL;

esp_bd_addr_t peer_addr = {0};

void bt_app_hf_open_audio(void) {
    ESP_LOGI(BT_HF_TAG, "Python requested SCO Audio Link. Forcing phone to open channel...");
    esp_hf_client_connect_audio(peer_addr);
}

void bt_app_hf_push_audio(const uint8_t *buf, uint32_t sz) {
    if (rb_from_wifi) {
        // Retry sending to RingBuffer to prevent dropping audio packets (fixes segmenting/choppiness).
        // This gracefully blocks the TCP stack and forces Python to naturally pace its stream!
        while (xRingbufferSend(rb_from_wifi, (void *)buf, sz, pdMS_TO_TICKS(50)) != pdTRUE) {
            if (!is_tcp_connected) return;
        }
        esp_hf_client_outgoing_data_ready();
    }
}

static uint32_t bt_app_hf_client_outgoing_cb(uint8_t *p_buf, uint32_t sz) {
    if (!rb_from_wifi) {
        memset(p_buf, 0, sz);
        return sz;
    }

    uint32_t bytes_read = 0;

    while (bytes_read < sz) {
        size_t item_size = 0;
        uint8_t *data = xRingbufferReceiveUpTo(rb_from_wifi, &item_size, 0, sz - bytes_read);

        if (data) {
            memcpy(p_buf + bytes_read, data, item_size);
            vRingbufferReturnItem(rb_from_wifi, data);
            bytes_read += item_size;
        } else {
            break;
        }
    }

    if (bytes_read < sz) {
        memset(p_buf + bytes_read, 0, sz - bytes_read);
    }

    return sz;
}

static void bt_app_hf_client_incoming_cb(const uint8_t *buf, uint32_t sz) {
    if (rb_to_wifi) {
        xRingbufferSend(rb_to_wifi, (void *)buf, sz, 0);
    }
}

void bt_app_hf_audio_init(void) {
    if (!rb_to_wifi) rb_to_wifi = xRingbufferCreate(32768, RINGBUF_TYPE_BYTEBUF);
    if (!rb_from_wifi) rb_from_wifi = xRingbufferCreate(32768, RINGBUF_TYPE_BYTEBUF);

    esp_hf_client_register_data_callback(bt_app_hf_client_incoming_cb, bt_app_hf_client_outgoing_cb);
}

void bt_app_hf_client_cb(esp_hf_client_cb_event_t event, esp_hf_client_cb_param_t *param) {
    switch (event) {
        case ESP_HF_CLIENT_CONNECTION_STATE_EVT:
            if (param->conn_stat.state == ESP_HF_CLIENT_CONNECTION_STATE_CONNECTED) {
                memcpy(peer_addr, param->conn_stat.remote_bda, ESP_BD_ADDR_LEN);
            }
            break;
        case ESP_HF_CLIENT_CIND_CALL_EVT:
            if (is_tcp_connected && sock_ctrl != -1) {
                if (param->call.status == ESP_HF_CALL_STATUS_CALL_IN_PROGRESS) {
                    send(sock_ctrl, "CALL_ACTIVE\n", 12, 0);
                } else {
                    send(sock_ctrl, "CALL_ENDED\n", 11, 0);
                }
            }
            break;
        case ESP_HF_CLIENT_CIND_CALL_SETUP_EVT:
            if (param->call_setup.status == ESP_HF_CALL_SETUP_STATUS_INCOMING && is_tcp_connected && sock_ctrl != -1) {
                send(sock_ctrl, "RING\n", 5, 0);
            }
            break;
        default: break;
    }
}