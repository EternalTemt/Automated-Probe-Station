/* USER CODE BEGIN Header */
/**
  ******************************************************************************
  * @file           : main.c
  * @brief          : Main program body
  ******************************************************************************
  * @attention
  *
  * Copyright (c) 2026 STMicroelectronics.
  * All rights reserved.
  *
  * This software is licensed under terms that can be found in the LICENSE file
  * in the root directory of this software component.
  * If no LICENSE file comes with this software, it is provided AS-IS.
  *
  ******************************************************************************
  */
/* USER CODE END Header */
/* Includes ------------------------------------------------------------------*/
#include "main.h"
#include "usb_device.h"

/* Private includes ----------------------------------------------------------*/
/* USER CODE BEGIN Includes */
#include <string.h>
#include <stdio.h>
#include "usbd_cdc_if.h"
/* USER CODE END Includes */

/* Private typedef -----------------------------------------------------------*/
/* USER CODE BEGIN PTD */

/* USER CODE END PTD */

/* Private define ------------------------------------------------------------*/
/* USER CODE BEGIN PD */
#define CMD_BUFFER_SIZE 128
/* USER CODE END PD */

/* Private macro -------------------------------------------------------------*/
/* USER CODE BEGIN PM */

/* USER CODE END PM */

/* Private variables ---------------------------------------------------------*/

/* USER CODE BEGIN PV */
volatile uint8_t cmd_ready;
char cmd_buffer[CMD_BUFFER_SIZE];
/* USER CODE END PV */

/* Private function prototypes -----------------------------------------------*/
void SystemClock_Config(void);
static void MX_GPIO_Init(void);
static void MX_SPI1_Init(void);
/* USER CODE BEGIN PFP */

/* USER CODE END PFP */

/* Private user code ---------------------------------------------------------*/
/* USER CODE BEGIN 0 */

// Базовые адреса по даташиту MCP23S17:
#define MCP_OPCODE_WRITE 0x40  // Команда записи, если A0=A1=A2 на земле
#define MCP_REG_IODIRA   0x00  // Регистр направления Порта A (0 = выход, 1 = вход)
#define MCP_REG_IODIRB   0x01  // Регистр направления Порта B (0 = выход, 1 = вход)
#define MCP_REG_OLATA    0x14  // Выходы Порта A
#define MCP_REG_OLATB    0x15  // Выходы Порта B

// ВОТ ОНА — САМА ФУНКЦИЯ:
// Быстрая отправка 1 байта в регистры SPI1
static inline void SPI1_LL_WriteByte(uint8_t byte)
{
  // 1. Ждём, пока освободится буфер передачи (TX Empty)
  while (!LL_SPI_IsActiveFlag_TXE(SPI1));

  // 2. Пишем байт напрямую в регистр данных
  LL_SPI_TransmitData8(SPI1, byte);

  // 3. Ждём физического завершения передачи последнего бита (Not Busy)
  while (LL_SPI_IsActiveFlag_BSY(SPI1));
}
// Функция начальной настройки чипа:
void MCP23S17_WriteReg(uint8_t reg_addr, uint8_t data)
{
  // 1. Опускаем CS (PA0) в 0 через атомарный сброс (Reset Output Pin)
  LL_GPIO_ResetOutputPin(GPIOB, LL_GPIO_PIN_0);

  // 2. Выталкиваем 3 байта подряд
  SPI1_LL_WriteByte(MCP_OPCODE_WRITE); // Байт адреса чипа
  SPI1_LL_WriteByte(reg_addr);         // Номер регистра
  SPI1_LL_WriteByte(data);             // Данные

  // 3. Поднимаем CS (PA0) обратно в 1 (Set Output Pin)
  LL_GPIO_SetOutputPin(GPIOB, LL_GPIO_PIN_0);
}

void MCP23S17_Init(void)
{
  // Убеждаемся, что модуль SPI1 включен
  if (!LL_SPI_IsEnabled(SPI1))
  {
    LL_SPI_Enable(SPI1);
  }

  // Настраиваем порты A и B как выходы
  MCP23S17_WriteReg(MCP_REG_IODIRA, 0x00);
  MCP23S17_WriteReg(MCP_REG_IODIRB, 0x00);

  // Сбрасываем выходы в 0 (драйверы DRV8835 в покое)
  MCP23S17_WriteReg(MCP_REG_OLATA, 0x00);
  MCP23S17_WriteReg(MCP_REG_OLATB, 0x00);
}

// Функция безопасного импульса на катушку реле K1..K8
// relay_num: 1..8
// state: 1 = SET (замкнуть), 0 = RESET (разомкнуть)
void Relay_Pulse(uint8_t relay_num, uint8_t state)
{
  if (relay_num < 1 || relay_num > 8) return;

  // На каждое реле уходит 2 бита (пара входов AIN1 / AIN2 у DRV8835)
  // Реле 1-4 сидят на порту A (GPA0-GPA7), реле 5-8 на порту B (GPB0-GPB7)
  uint8_t reg = (relay_num <= 4) ? MCP_REG_OLATA : MCP_REG_OLATB;
  uint8_t bit_pair = (relay_num <= 4) ? (relay_num - 1) * 2 : (relay_num - 5) * 2;

  // Если SET: 1A11 = 1, 1A12 = 0 -> сдвигаем биты (01b)
  // Если RESET: 1A11 = 0, 1A12 = 1 -> сдвигаем биты (10b)
  uint8_t mask = (state ? 0x01 : 0x02) << bit_pair;

  // 1. Подаём импульс переключения на мост
  MCP23S17_WriteReg(reg, mask);

  // 2. Ждём 15 мс (время срабатывания якоря IM43GR)
  HAL_Delay(15);

  // 3. ОБЯЗАТЕЛЬНО обесточиваем катушку (входы моста 0, 0)
  MCP23S17_WriteReg(reg, 0x00);
}

void Handle_Command(char* cmd)
{
  char resp[64];

  // Проверка связи
  if (strcmp(cmd, "PING") == 0)
  {
    int len = snprintf(resp, sizeof(resp), "PONG\r\n");
    CDC_Transmit_FS((uint8_t*)resp, len);
  }
  // Управление реле: RELAY <номер 1..8> <SET или RESET>
  else if (strncmp(cmd, "RELAY ", 6) == 0)
  {
    int num = 0;
    char action[8] = {0};

    // Читаем номер реле и действие
    if (sscanf(&cmd[6], "%d %7s", &num, action) == 2)
    {
      if (num >= 1 && num <= 8)
      {
        if (strcmp(action, "SET") == 0)
        {
          Relay_Pulse(num, 1);
          int len = snprintf(resp, sizeof(resp), "OK: Relay %d SET\r\n", num);
          CDC_Transmit_FS((uint8_t*)resp, len);
        }
        else if (strcmp(action, "RESET") == 0)
        {
          Relay_Pulse(num, 0);
          int len = snprintf(resp, sizeof(resp), "OK: Relay %d RESET\r\n", num);
          CDC_Transmit_FS((uint8_t*)resp, len);
        }
        else
        {
          int len = snprintf(resp, sizeof(resp), "ERR: Unknown action (use SET/RESET)\r\n");
          CDC_Transmit_FS((uint8_t*)resp, len);
        }
      }
      else
      {
        int len = snprintf(resp, sizeof(resp), "ERR: Relay number must be 1..8\r\n");
        CDC_Transmit_FS((uint8_t*)resp, len);
      }
    }
    else
    {
      int len = snprintf(resp, sizeof(resp), "ERR: Invalid format (use RELAY <1-8> <SET/RESET>)\r\n");
      CDC_Transmit_FS((uint8_t*)resp, len);
    }
  }
  // Неизвестная команда
  else
  {
    int len = snprintf(resp, sizeof(resp), "ERR: Unknown command\r\n");
    CDC_Transmit_FS((uint8_t*)resp, len);
  }
}
/* USER CODE END 0 */

/**
  * @brief  The application entry point.
  * @retval int
  */
int main(void)
{

  /* USER CODE BEGIN 1 */

  /* USER CODE END 1 */

  /* MCU Configuration--------------------------------------------------------*/

  /* Reset of all peripherals, Initializes the Flash interface and the Systick. */
  HAL_Init();

  /* USER CODE BEGIN Init */

  /* USER CODE END Init */

  /* Configure the system clock */
  SystemClock_Config();

  /* USER CODE BEGIN SysInit */

  /* USER CODE END SysInit */

  /* Initialize all configured peripherals */
  MX_GPIO_Init();
  MX_USB_DEVICE_Init();
  MX_SPI1_Init();
  /* USER CODE BEGIN 2 */
  MCP23S17_Init(); // Инициализация расширителя портов MCP23S17
  /* USER CODE END 2 */

  /* Infinite loop */
  /* USER CODE BEGIN WHILE */
  while (1)
  {
    // Проверяем, выставило ли прерывание флаг готовности строки
    if (cmd_ready)
    {
      Handle_Command(cmd_buffer);
        
      // Сбрасываем флаг, чтобы не выполнять команду повторно
      cmd_ready = 0;
    }

    /* USER CODE END WHILE */

    /* USER CODE BEGIN 3 */
  }
  /* USER CODE END 3 */
}

/**
  * @brief System Clock Configuration
  * @retval None
  */
void SystemClock_Config(void)
{
  LL_FLASH_SetLatency(LL_FLASH_LATENCY_1);
  while(LL_FLASH_GetLatency()!= LL_FLASH_LATENCY_1)
  {
  }
  LL_RCC_HSE_Enable();

   /* Wait till HSE is ready */
  while(LL_RCC_HSE_IsReady() != 1)
  {

  }
  LL_RCC_PLL_ConfigDomain_SYS(LL_RCC_PLLSOURCE_HSE_DIV_1, LL_RCC_PLL_MUL_6);
  LL_RCC_PLL_Enable();

   /* Wait till PLL is ready */
  while(LL_RCC_PLL_IsReady() != 1)
  {

  }
  LL_RCC_SetAHBPrescaler(LL_RCC_SYSCLK_DIV_1);
  LL_RCC_SetAPB1Prescaler(LL_RCC_APB1_DIV_2);
  LL_RCC_SetAPB2Prescaler(LL_RCC_APB2_DIV_1);
  LL_RCC_SetSysClkSource(LL_RCC_SYS_CLKSOURCE_PLL);

   /* Wait till System clock is ready */
  while(LL_RCC_GetSysClkSource() != LL_RCC_SYS_CLKSOURCE_STATUS_PLL)
  {

  }
  LL_SetSystemCoreClock(48000000);

   /* Update the time base */
  if (HAL_InitTick (TICK_INT_PRIORITY) != HAL_OK)
  {
    Error_Handler();
  }
  LL_RCC_SetUSBClockSource(LL_RCC_USB_CLKSOURCE_PLL);
}

/**
  * @brief SPI1 Initialization Function
  * @param None
  * @retval None
  */
static void MX_SPI1_Init(void)
{

  /* USER CODE BEGIN SPI1_Init 0 */

  /* USER CODE END SPI1_Init 0 */

  LL_SPI_InitTypeDef SPI_InitStruct = {0};

  LL_GPIO_InitTypeDef GPIO_InitStruct = {0};

  /* Peripheral clock enable */
  LL_APB2_GRP1_EnableClock(LL_APB2_GRP1_PERIPH_SPI1);

  LL_APB2_GRP1_EnableClock(LL_APB2_GRP1_PERIPH_GPIOA);
  /**SPI1 GPIO Configuration
  PA5   ------> SPI1_SCK
  PA7   ------> SPI1_MOSI
  */
  GPIO_InitStruct.Pin = LL_GPIO_PIN_5|LL_GPIO_PIN_7;
  GPIO_InitStruct.Mode = LL_GPIO_MODE_ALTERNATE;
  GPIO_InitStruct.Speed = LL_GPIO_SPEED_FREQ_HIGH;
  GPIO_InitStruct.OutputType = LL_GPIO_OUTPUT_PUSHPULL;
  LL_GPIO_Init(GPIOA, &GPIO_InitStruct);

  /* USER CODE BEGIN SPI1_Init 1 */

  /* USER CODE END SPI1_Init 1 */
  /* SPI1 parameter configuration*/
  SPI_InitStruct.TransferDirection = LL_SPI_FULL_DUPLEX;
  SPI_InitStruct.Mode = LL_SPI_MODE_MASTER;
  SPI_InitStruct.DataWidth = LL_SPI_DATAWIDTH_8BIT;
  SPI_InitStruct.ClockPolarity = LL_SPI_POLARITY_LOW;
  SPI_InitStruct.ClockPhase = LL_SPI_PHASE_1EDGE;
  SPI_InitStruct.NSS = LL_SPI_NSS_SOFT;
  SPI_InitStruct.BaudRate = LL_SPI_BAUDRATEPRESCALER_DIV32;
  SPI_InitStruct.BitOrder = LL_SPI_MSB_FIRST;
  SPI_InitStruct.CRCCalculation = LL_SPI_CRCCALCULATION_DISABLE;
  SPI_InitStruct.CRCPoly = 10;
  LL_SPI_Init(SPI1, &SPI_InitStruct);
  /* USER CODE BEGIN SPI1_Init 2 */

  /* USER CODE END SPI1_Init 2 */

}

/**
  * @brief GPIO Initialization Function
  * @param None
  * @retval None
  */
static void MX_GPIO_Init(void)
{
  LL_GPIO_InitTypeDef GPIO_InitStruct = {0};
  /* USER CODE BEGIN MX_GPIO_Init_1 */

  /* USER CODE END MX_GPIO_Init_1 */

  /* GPIO Ports Clock Enable */
  LL_APB2_GRP1_EnableClock(LL_APB2_GRP1_PERIPH_GPIOC);
  LL_APB2_GRP1_EnableClock(LL_APB2_GRP1_PERIPH_GPIOD);
  LL_APB2_GRP1_EnableClock(LL_APB2_GRP1_PERIPH_GPIOA);
  LL_APB2_GRP1_EnableClock(LL_APB2_GRP1_PERIPH_GPIOB);

  /**/
  LL_GPIO_ResetOutputPin(GPIOC, LL_GPIO_PIN_13);

  /**/
  LL_GPIO_ResetOutputPin(GPIOB, LL_GPIO_PIN_0);

  /**/
  GPIO_InitStruct.Pin = LL_GPIO_PIN_13;
  GPIO_InitStruct.Mode = LL_GPIO_MODE_OUTPUT;
  GPIO_InitStruct.Speed = LL_GPIO_SPEED_FREQ_LOW;
  GPIO_InitStruct.OutputType = LL_GPIO_OUTPUT_PUSHPULL;
  GPIO_InitStruct.Pull = LL_GPIO_PULL_UP;
  LL_GPIO_Init(GPIOC, &GPIO_InitStruct);

  /**/
  GPIO_InitStruct.Pin = LL_GPIO_PIN_0;
  GPIO_InitStruct.Mode = LL_GPIO_MODE_OUTPUT;
  GPIO_InitStruct.Speed = LL_GPIO_SPEED_FREQ_LOW;
  GPIO_InitStruct.OutputType = LL_GPIO_OUTPUT_PUSHPULL;
  GPIO_InitStruct.Pull = LL_GPIO_PULL_UP;
  LL_GPIO_Init(GPIOB, &GPIO_InitStruct);

  /* USER CODE BEGIN MX_GPIO_Init_2 */

  /* USER CODE END MX_GPIO_Init_2 */
}

/* USER CODE BEGIN 4 */

/* USER CODE END 4 */

/**
  * @brief  This function is executed in case of error occurrence.
  * @retval None
  */
void Error_Handler(void)
{
  /* USER CODE BEGIN Error_Handler_Debug */
  /* User can add his own implementation to report the HAL error return state */
  __disable_irq();
  while (1)
  {
  }
  /* USER CODE END Error_Handler_Debug */
}
#ifdef USE_FULL_ASSERT
/**
  * @brief  Reports the name of the source file and the source line number
  *         where the assert_param error has occurred.
  * @param  file: pointer to the source file name
  * @param  line: assert_param error line source number
  * @retval None
  */
void assert_failed(uint8_t *file, uint32_t line)
{
  /* USER CODE BEGIN 6 */
  /* User can add his own implementation to report the file name and line number,
     ex: printf("Wrong parameters value: file %s on line %d\r\n", file, line) */
  /* USER CODE END 6 */
}
#endif /* USE_FULL_ASSERT */
