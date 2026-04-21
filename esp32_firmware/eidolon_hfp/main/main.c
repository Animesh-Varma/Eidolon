#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "sdkconfig.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "freertos/event_groups.h"
#include "freertos/ringbuf.h"
#include "nvs.h"
#include "nvs_flash.h"
#include "esp_system.h"
#include "esp_wifi.h"
#include "esp_event.h"
#include "esp_log.h"
#include "esp_bt.h"
#include "bt_app_core.h"
#include "esp_bt_main.h"
#include "esp_bt_device.h"
#include "esp_gap_bt_api.h"
#include "esp_hf_client_api.h"
#include "bt_app_hf.h"
#include "lwip/sockets.h"
#include "lwip/tcp.h"
#include <arpa/inet.h>
#include "esp_coexist.h"
#include "wifi_credentials.h"

#define PORT_AUDIO     8000
#define PORT_CTRL      8001

static const char *TAG = "EIDOLON_BRIDGE";

int sock_audio = -1;
int sock_ctrl = -1;
volatile bool is_tcp_connected = false;

static EventGroupHandle_t wifi_event_group;
#define WIFI_CONNECTED_BIT BIT0

extern void bt_app_hf_push_audio(const uint8_t *buf, uint32_t sz);
extern void bt_app_hf_audio_init(void);
extern void bt_app_hf_open_audio(void);
extern RingbufHandle_t rb_to_wifi;

static void wifi_event_handler(void* arg, esp_event_base_t event_base, int32_t event_id, void* event_data) {
    if (event_base == WIFI_EVENT && event_id == WIFI_EVENT_STA_START) esp_wifi_connect();
    else if (event_base == WIFI_EVENT && event_id == WIFI_EVENT_STA_DISCONNECTED) esp_wifi_connect();
    else if (event_base == IP_EVENT && event_id == IP_EVENT_STA_GOT_IP) xEventGroupSetBits(wifi_event_group, WIFI_CONNECTED_BIT);
}

static void wifi_init_sta(void) {
    esp_netif_init();
    esp_event_loop_create_default();
    esp_netif_create_default_wifi_sta();
    wifi_init_config_t cfg = WIFI_INIT_CONFIG_DEFAULT();
    esp_wifi_init(&cfg);
    esp_event_handler_instance_register(WIFI_EVENT, ESP_EVENT_ANY_ID, &wifi_event_handler, NULL, NULL);
    esp_event_handler_instance_register(IP_EVENT, IP_EVENT_STA_GOT_IP, &wifi_event_handler, NULL, NULL);
    wifi_config_t wifi_config = { .sta = { .ssid = WIFI_SSID, .password = WIFI_PASS, }, };
    esp_wifi_set_mode(WIFI_MODE_STA);
    esp_wifi_set_config(WIFI_IF_STA, &wifi_config);
    esp_wifi_start();
}

static void tcp_client_task(void *pvParameters) {
    struct sockaddr_in dest_audio = { .sin_family = AF_INET, .sin_port = htons(PORT_AUDIO) };
    dest_audio.sin_addr.s_addr = inet_addr(HOST_IP);
    struct sockaddr_in dest_ctrl = { .sin_family = AF_INET, .sin_port = htons(PORT_CTRL) };
    dest_ctrl.sin_addr.s_addr = inet_addr(HOST_IP);

    uint8_t *rx_buffer = (uint8_t *)malloc(4096);
    uint8_t leftover_byte = 0;
    bool has_leftover = false;

    while (1) {
        sock_ctrl = socket(AF_INET, SOCK_STREAM, IPPROTO_IP);
        sock_audio = socket(AF_INET, SOCK_STREAM, IPPROTO_IP);

        int flag = 1;
        setsockopt(sock_audio, IPPROTO_TCP, TCP_NODELAY, &flag, sizeof(int));
        setsockopt(sock_ctrl, IPPROTO_TCP, TCP_NODELAY, &flag, sizeof(int));

        has_leftover = false;

        if (connect(sock_ctrl, (struct sockaddr *)&dest_ctrl, sizeof(dest_ctrl)) == 0 &&
            connect(sock_audio, (struct sockaddr *)&dest_audio, sizeof(dest_audio)) == 0) {

            ESP_LOGI(TAG, "Successfully connected to Python sockets!");
            is_tcp_connected = true;

            while (is_tcp_connected) {
                int offset = has_leftover ? 1 : 0;
                int len = recv(sock_audio, rx_buffer + offset, 4096 - offset, 0);
                if (len <= 0) break;

                int total_len = len + offset;
                has_leftover = (total_len % 2 != 0);
                int push_len = total_len - (has_leftover ? 1 : 0);

                if (has_leftover) leftover_byte = rx_buffer[push_len];
                if (push_len > 0) bt_app_hf_push_audio(rx_buffer, push_len);
                if (has_leftover) rx_buffer[0] = leftover_byte;
            }
        }

        is_tcp_connected = false;
        if (sock_ctrl != -1) { close(sock_ctrl); sock_ctrl = -1; }
        if (sock_audio != -1) { close(sock_audio); sock_audio = -1; }
        vTaskDelay(pdMS_TO_TICKS(2000));
    }
}

static void tcp_audio_tx_task(void *pvParameters) {
    while (1) {
        if (is_tcp_connected && sock_audio != -1 && rb_to_wifi != NULL) {
            size_t size = 0;
            uint8_t *data = xRingbufferReceiveUpTo(rb_to_wifi, &size, pdMS_TO_TICKS(20), 4096);
            if (data) {
                send(sock_audio, data, size, 0);
                vRingbufferReturnItem(rb_to_wifi, data);
            }
        } else {
            vTaskDelay(pdMS_TO_TICKS(50));
        }
    }
}

static void tcp_ctrl_rx_task(void *pvParameters) {
    uint8_t rx_buf[128];
    while (1) {
        if (is_tcp_connected && sock_ctrl != -1) {
            int len = recv(sock_ctrl, rx_buf, sizeof(rx_buf) - 1, MSG_DONTWAIT);
            if (len > 0) {
                rx_buf[len] = '\0';
                if (strstr((char *)rx_buf, "ATA")) esp_hf_client_answer_call();
                else if (strstr((char *)rx_buf, "AT+CHUP")) esp_hf_client_reject_call();
                else if (strstr((char *)rx_buf, "OPEN_AUDIO")) bt_app_hf_open_audio();
            } else {
                vTaskDelay(pdMS_TO_TICKS(100));
            }
        } else {
            vTaskDelay(pdMS_TO_TICKS(500));
        }
    }
}

void esp_bt_gap_cb(esp_bt_gap_cb_event_t event, esp_bt_gap_cb_param_t *param) {
    if (event == ESP_BT_GAP_CFM_REQ_EVT) esp_bt_gap_ssp_confirm_reply(param->cfm_req.bda, true);
    if (event == ESP_BT_GAP_PIN_REQ_EVT) {
        esp_bt_pin_code_t pin_code = {'0', '0', '0', '0'};
        esp_bt_gap_pin_reply(param->pin_req.bda, true, 4, pin_code);
    }
}

static void bt_hf_client_hdl_stack_evt(uint16_t event, void *p_param) {
    if (event == 0) {
        esp_bt_dev_set_device_name("ESP_EIDOLON_BRIDGE");
        esp_bt_sp_param_t param_type = ESP_BT_SP_IOCAP_MODE;
        esp_bt_io_cap_t iocap = ESP_BT_IO_CAP_NONE;
        esp_bt_gap_set_security_param(param_type, &iocap, sizeof(uint8_t));
        esp_bt_gap_set_scan_mode(ESP_BT_CONNECTABLE, ESP_BT_GENERAL_DISCOVERABLE);
    }
}

void app_main(void) {
    ESP_ERROR_CHECK(nvs_flash_init());
    wifi_event_group = xEventGroupCreate();
    wifi_init_sta();

    xEventGroupWaitBits(wifi_event_group, WIFI_CONNECTED_BIT, pdFALSE, pdTRUE, portMAX_DELAY);

    xTaskCreatePinnedToCore(tcp_client_task, "tcp_rx", 8192, NULL, 5, NULL, 1);
    xTaskCreatePinnedToCore(tcp_audio_tx_task, "tcp_tx", 8192, NULL, 5, NULL, 1);
    xTaskCreatePinnedToCore(tcp_ctrl_rx_task, "tcp_ctrl", 4096, NULL, 5, NULL, 1);

    ESP_ERROR_CHECK(esp_bt_controller_mem_release(ESP_BT_MODE_BLE));
    esp_bt_controller_config_t bt_cfg = BT_CONTROLLER_INIT_CONFIG_DEFAULT();
    ESP_ERROR_CHECK(esp_bt_controller_init(&bt_cfg));
    ESP_ERROR_CHECK(esp_bt_controller_enable(ESP_BT_MODE_CLASSIC_BT));

    // Force Bluetooth to have Priority over the Antenna to prevent Wi-Fi dropouts
    esp_coex_preference_set(ESP_COEX_PREFER_BT);

    esp_bluedroid_config_t bluedroid_cfg = BT_BLUEDROID_INIT_CONFIG_DEFAULT();
    bluedroid_cfg.ssp_en = true;
    ESP_ERROR_CHECK(esp_bluedroid_init_with_cfg(&bluedroid_cfg));
    ESP_ERROR_CHECK(esp_bluedroid_enable());

    bt_app_task_start_up();
    esp_bt_gap_register_callback(esp_bt_gap_cb);
    esp_hf_client_register_callback(bt_app_hf_client_cb);
    esp_hf_client_init();
    bt_app_hf_audio_init();

    bt_app_work_dispatch(bt_hf_client_hdl_stack_evt, 0, NULL, 0, NULL);
}