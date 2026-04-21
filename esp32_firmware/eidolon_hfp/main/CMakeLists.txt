# Forcefully locate all .c files in the directory
file(GLOB project_sources "*.c")

idf_component_register(SRCS ${project_sources}
                       INCLUDE_DIRS "."
                       REQUIRES
                            nvs_flash
                            esp_wifi
                            esp_event
                            esp_netif
                            bt
                            lwip
                            esp_coex
                            console
                            esp_ringbuf
                            freertos
                            driver
                            esp_hw_support
                            esp_system
                            esp_common)