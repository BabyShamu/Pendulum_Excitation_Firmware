#include "stm32g4xx_hal.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <math.h>

// STEP = D5 = PB4
#define STEP_PORT GPIOB
#define STEP_PIN  GPIO_PIN_4

// DIR = D6 = PB10
#define DIR_PORT  GPIOB
#define DIR_PIN   GPIO_PIN_10
#define DIR_UP    0U
#define DIR_DOWN  1U

// UPPER LIMIT = D7 = PA8; LOWER LIMIT = D8 = PA9.
// Wiring: COM -> GND, NO -> GPIO with internal pull-up enabled.
#define UPPER_LIMIT_PORT GPIOA
#define UPPER_LIMIT_PIN  GPIO_PIN_8
#define LOWER_LIMIT_PORT GPIOA
#define LOWER_LIMIT_PIN  GPIO_PIN_9

// NUCLEO LD2 LED
#define LED_PORT  GPIOA
#define LED_PIN   GPIO_PIN_5

// ST-LINK VCP USART2: TX=PA2, RX=PA3
#define UART_BAUDRATE 115200U
#define UART_RX_BUF_SIZE 80U
#define UART_RX_RING_SIZE 128U
#define UART_TX_RING_SIZE 1024U

// Set to 1 for normal level-shifter outputs.
// Set to 0 if your stage inverts logic.
#define SHIFTER_NON_INVERTED 1U

// Live-tunable defaults
#define DEFAULT_TARGET_HZ   350U
#define DEFAULT_ACCEL_HZPS  900U
#define CONTROL_PERIOD_MS   10U
#define MAX_HOMING_STEPS    20000U
#define JOG_SPEED_HZ        150U
#define HOMING_SPEED_HZ     1100U
#define PARAMETRIC_SPEED_HZ 4000U
#define PARAMETRIC_UPDATE_PERIOD_MS   10U
#define PARAMETRIC_TEST_PERIOD_MS      5U
#define PARAMETRIC_TEST_TELEMETRY_DECIMATION 4U
#define PARAMETRIC_TEST_AGC_STEP       0.0005f
#define PARAMETRIC_TEST_AGC_Z_EPSILON  1e-20f
#define PARAMETRIC_TEST_AGC_MAX_LOG_GAIN 6.9077553f
#define PARAMETRIC_TEST_SATURATION     3.0f
#define PARAMETRIC_TEST_PHASE_COS      0.9238795f
#define PARAMETRIC_TEST_PHASE_SIN      0.3826834f
#define PARAMETRIC_TEST_OUTPUT_SATURATION 2.0f
#define PARAMETRIC_TEST_MASTER_GAIN       2.0f
#define PARAMETRIC_TEST_RATE_DELTA_RAD    0.1f
#define PARAMETRIC_TEST_PINION_RADIUS_MM 10.0f
#define PARAMETRIC_TEST_STEPS_PER_MM     40.0f
#define CLOSED_LOOP_MAX_MM               7.5f
#define CLOSED_LOOP_COMMAND_SIGN        (-1.0f)
#define CLOSED_LOOP_TRAVEL_MARGIN_MM     2.0f
#define CLOSED_LOOP_MAX_STEP_HZ          6000U
#define CLOSED_LOOP_STEP_PULSE_US        5U
#define CLOSED_LOOP_DIR_SETUP_US         10U
#define CLOSED_LOOP_ANGLE_FAILURE_LIMIT  4U
#define CLOSED_LOOP_PHASE_IDLE           0U
#define CLOSED_LOOP_PHASE_DIR_SETUP      1U
#define CLOSED_LOOP_PHASE_STEP_HIGH      2U
#define CLOSED_LOOP_PHASE_INTERSTEP      3U
#define LIMIT_DEBOUNCE_MS                8U
#define PARAMETRIC_ANGLE_TIME_CONST_S 0.05f
#define PARAMETRIC_DC_BLOCK_TIME_CONST_S 2.0f
#define PARAMETRIC_AGC_TIME_CONST_S   1.0f
#define PARAMETRIC_AGC_MIN_PEAK       1e-6f
#define PARAMETRIC_SLEW_MM_PER_SEC    250.0f
#define LIVE_PERIOD_MS      20U
#define DEFAULT_STEPS_PER_MM 40.0f
#define MAX_SINE_FREQUENCY_HZ 10.0f
#define PI_F                3.14159265358979323846f

static void GPIO_Init(void);
static void USART2_Init(void);
static void I2C1_Init(void);
static void ClosedLoopStepTimerInit(void);
static void ClosedLoopActuatorKick(void);
static void ClosedLoopActuatorStop(void);
static void ClosedLoopActuatorOnTimer(void);
static GPIO_PinState ActiveState(uint8_t on);
static uint8_t UpperLimitPressed(void);
static uint8_t LowerLimitPressed(void);
static void UpdateLimitDebounce(void);
static uint8_t UpperLimitPressedDebounced(void);
static uint8_t LowerLimitPressedDebounced(void);
static uint8_t WaitForDebouncedLimit(uint8_t upperLimit);
static HAL_StatusTypeDef AS5600_ReadAngle(uint16_t *angle);
static void SetDirection(uint8_t dir);
static uint8_t StepOne(uint32_t hz, uint8_t stopUpper, uint8_t stopLower);
static void Jog(uint8_t dir, uint32_t steps);
static void Home(void);
static void ReturnToCenter(void);
static void UpdateSine(void);
static void UpdateParametric(void);
static void UpdateParametricTest(void);
static void UpdateParametricClosedLoop(void);
static void ReportClosedLoopFault(void);
static float ParametricTestDcBlockApprox(float input, float dt, uint8_t reset);
static float ParametricTestAgc(float input, float *logGain);
static void DelayUs(uint32_t us);
static void PollUart(void);
static void ProcessLine(char *line);
static void PrintHelp(void);
static void PrintStatus(void);
static void PrintSwitches(void);
static void ReportSwitchChanges(void);
static void PrintAngle(void);
static void PrintLiveTelemetry(void);
static void SetAngleZero(void);
static float RelativeAngleDegrees(uint16_t rawAngle);
static void UartPrint(const char *text);
static void Error_Handler(void);

static UART_HandleTypeDef huart2;
static DMA_HandleTypeDef hdma_usart2_rx;
static I2C_HandleTypeDef hi2c1;
static char g_rxBuf[UART_RX_BUF_SIZE];
static uint32_t g_rxIdx = 0U;
static uint8_t g_rxLineTooLong = 0U;
static uint8_t g_lastRxWasCr = 0U;
static uint8_t g_uartDmaRxBuf[64U];
static volatile uint8_t g_uartRxRing[UART_RX_RING_SIZE];
static volatile uint16_t g_uartRxHead = 0U;
static volatile uint16_t g_uartRxTail = 0U;
static volatile uint8_t g_uartRxOverflow = 0U;
static volatile uint32_t g_uartRxOverflowCount = 0U;
static uint8_t g_uartTxRing[UART_TX_RING_SIZE];
static volatile uint16_t g_uartTxHead = 0U;
static volatile uint16_t g_uartTxTail = 0U;
static volatile uint8_t g_uartTxBusy = 0U;
static volatile uint16_t g_uartTxInFlight = 0U;

static uint32_t g_targetHz = DEFAULT_TARGET_HZ;
static uint32_t g_currentHz = 50U;
static uint32_t g_accelHzPerSec = DEFAULT_ACCEL_HZPS;
static uint8_t g_run = 0U;
static volatile uint8_t g_dir = 0U;
static uint8_t g_homed = 0U;
static uint8_t g_sineRunning = 0U;
static uint32_t g_travelSteps = 0U;
static volatile int32_t g_positionSteps = 0;
static float g_sineAmplitudeMm = 0.0f;
static float g_sineFrequencyHz = 0.0f;
static uint32_t g_sineStartMs = 0U;
static uint8_t g_parametricRunning = 0U;
static float g_parametricAmplitudeMm = 0.0f;
static float g_parametricPhiRad = 0.0f;
static uint32_t g_parametricLastUpdateMs = 0U;
static float g_parametricFilteredThetaRad = 0.0f;
static uint8_t g_parametricFilterValid = 0U;
static float g_parametricCommandedMm = 0.0f;
static float g_parametricThetaSqDc = 0.0f;
static float g_parametricPrevAcSignal = 0.0f;
static float g_parametricCosPeak = 0.0f;
static float g_parametricSinPeak = 0.0f;
static uint8_t g_parametricTestRunning = 0U;
static uint8_t g_parametricTestFilterValid = 0U;
static uint32_t g_parametricTestStartMs = 0U;
static uint32_t g_parametricTestLastSampleMs = 0U;
static float g_parametricTestDcEstimate = 0.0f;
static float g_parametricTestPrevDcBlocked = 0.0f;
static float g_parametricTestQuadFiltered = 0.0f;
static float g_parametricTestDirectAgcLogGain = 0.0f;
static float g_parametricTestQuadAgcLogGain = 0.0f;
static float g_parametricTestRateLimitedRad = 0.0f;
static uint32_t g_parametricTestSampleCount = 0U;
static volatile uint8_t g_parametricClosedLoopRunning = 0U;
static volatile int32_t g_parametricClosedLoopTargetSteps = 0;
static volatile uint8_t g_closedLoopActuatorPhase = 0U;
static volatile uint8_t g_closedLoopFault = 0U;
static volatile uint8_t g_closedLoopFaultPending = 0U;
static volatile uint32_t g_closedLoopKickCount = 0U;
static volatile uint32_t g_closedLoopTimerArmCount = 0U;
static volatile uint32_t g_tim6IrqCount = 0U;
static volatile uint32_t g_closedLoopActuatorCallbackCount = 0U;
static volatile uint32_t g_closedLoopStepHighCount = 0U;
static volatile uint32_t g_closedLoopStepCompleteCount = 0U;
static uint8_t g_parametricClosedLoopFilterValid = 0U;
static uint32_t g_parametricClosedLoopStartMs = 0U;
static uint32_t g_parametricClosedLoopNextSampleMs = 0U;
static uint32_t g_parametricClosedLoopSampleCount = 0U;
static uint8_t g_parametricClosedLoopAngleFailureCount = 0U;
static float g_parametricClosedLoopDcEstimate = 0.0f;
static float g_parametricClosedLoopPrevDcBlocked = 0.0f;
static float g_parametricClosedLoopQuadFiltered = 0.0f;
static float g_parametricClosedLoopDirectAgcLogGain = 0.0f;
static float g_parametricClosedLoopQuadAgcLogGain = 0.0f;
static float g_parametricClosedLoopRateLimitedRad = 0.0f;
static uint8_t g_liveTelemetry = 0U;
static uint8_t g_recording = 0U;
static uint8_t g_suppressPromptOnce = 0U;
static uint32_t g_lastLiveMs = 0U;
static uint32_t g_recordStartMs = 0U;
static float g_stepsPerMm = DEFAULT_STEPS_PER_MM;
static uint8_t g_i2cReady = 0U;
static uint16_t g_angleZeroRaw = 0U;
static uint8_t g_angleZeroValid = 0U;
static uint8_t g_switchStatusValid = 0U;
static uint8_t g_lastUpperLimit = 0U;
static uint8_t g_lastLowerLimit = 0U;
static uint8_t g_limitDebounceInitialized = 0U;
static uint8_t g_upperLimitCandidate = 0U;
static uint8_t g_lowerLimitCandidate = 0U;
static uint8_t g_upperLimitDebounced = 0U;
static uint8_t g_lowerLimitDebounced = 0U;
static uint32_t g_upperLimitCandidateSinceMs = 0U;
static uint32_t g_lowerLimitCandidateSinceMs = 0U;

void SysTick_Handler(void)
{
    HAL_IncTick();
}

void USART2_IRQHandler(void)
{
    HAL_UART_IRQHandler(&huart2);
}

void DMA1_Channel6_IRQHandler(void)
{
    HAL_DMA_IRQHandler(&hdma_usart2_rx);
}

void TIM6_DAC_IRQHandler(void)
{
    g_tim6IrqCount++;
    if ((TIM6->SR & TIM_SR_UIF) != 0U)
    {
        TIM6->SR = 0U;
        ClosedLoopActuatorOnTimer();
    }
}

void HAL_UARTEx_RxEventCallback(UART_HandleTypeDef *huart, uint16_t Size)
{
    if (huart->Instance == USART2)
    {
        for (uint16_t i = 0U; i < Size; i++)
        {
            uint16_t nextHead = (uint16_t)((g_uartRxHead + 1U) % UART_RX_RING_SIZE);
            if (nextHead == g_uartRxTail)
            {
                g_uartRxOverflow = 1U;
                g_uartRxOverflowCount++;
                break;
            }
            g_uartRxRing[g_uartRxHead] = g_uartDmaRxBuf[i];
            g_uartRxHead = nextHead;
        }

        if (HAL_UARTEx_ReceiveToIdle_DMA(&huart2, g_uartDmaRxBuf, sizeof(g_uartDmaRxBuf)) != HAL_OK)
        {
            Error_Handler();
        }
        __HAL_DMA_DISABLE_IT(huart2.hdmarx, DMA_IT_HT);
    }
}

static void UartTxKick(void)
{
    uint16_t chunkLen;

    if (g_uartTxBusy != 0U || g_uartTxHead == g_uartTxTail)
    {
        return;
    }

    chunkLen = (g_uartTxHead > g_uartTxTail)
                   ? (uint16_t)(g_uartTxHead - g_uartTxTail)
                   : (uint16_t)(UART_TX_RING_SIZE - g_uartTxTail);

    g_uartTxBusy = 1U;
    g_uartTxInFlight = chunkLen;
    HAL_UART_Transmit_IT(&huart2, &g_uartTxRing[g_uartTxTail], chunkLen);
}

void HAL_UART_TxCpltCallback(UART_HandleTypeDef *huart)
{
    if (huart->Instance == USART2)
    {
        g_uartTxTail = (uint16_t)((g_uartTxTail + g_uartTxInFlight) % UART_TX_RING_SIZE);
        g_uartTxBusy = 0U;
        UartTxKick();
    }
}

int main(void)
{
    HAL_Init();
    GPIO_Init();
    ClosedLoopStepTimerInit();
    USART2_Init();
    UartPrint("\r\nStepper control starting\r\n");
    I2C1_Init();

    // Enable DWT cycle counter for precise short pulse timing.
    CoreDebug->DEMCR |= CoreDebug_DEMCR_TRCENA_Msk;
    DWT->CYCCNT = 0U;
    DWT->CTRL |= DWT_CTRL_CYCCNTENA_Msk;

    HAL_GPIO_WritePin(STEP_PORT, STEP_PIN, ActiveState(0U));
    SetDirection(g_dir);

    UartPrint(g_i2cReady != 0U ? "I2C1 ready\r\n" : "I2C1 unavailable; check SDA/SCL wiring\r\n");
    SetAngleZero();
    UartPrint("Stepper control ready\r\n");
    PrintHelp();
    UartPrint("> ");

    while (1)
    {
        ReportClosedLoopFault();
        if (g_parametricTestRunning == 0U && g_parametricClosedLoopRunning == 0U)
        {
            ReportSwitchChanges();
        }
        uint32_t stepHzPerTick = (g_accelHzPerSec * CONTROL_PERIOD_MS) / 1000U;
        if (stepHzPerTick == 0U)
        {
            stepHzPerTick = 1U;
        }

        if (g_currentHz < g_targetHz)
        {
            uint32_t diff = g_targetHz - g_currentHz;
            g_currentHz += (diff > stepHzPerTick) ? stepHzPerTick : diff;
        }
        else if (g_currentHz > g_targetHz)
        {
            uint32_t diff = g_currentHz - g_targetHz;
            g_currentHz -= (diff > stepHzPerTick) ? stepHzPerTick : diff;
        }

        PollUart();
        PrintLiveTelemetry();

        if (g_parametricClosedLoopRunning != 0U)
        {
            UpdateParametricClosedLoop();
        }
        else if (g_parametricTestRunning != 0U)
        {
            UpdateParametricTest();
        }
        else if (g_sineRunning != 0U)
        {
            UpdateSine();
        }
        else if (g_parametricRunning != 0U)
        {
            UpdateParametric();
        }
        else if (g_run != 0U && g_currentHz > 0U && UpperLimitPressed() == 0U && LowerLimitPressed() == 0U)
        {
            uint32_t pulses = (g_currentHz * CONTROL_PERIOD_MS) / 1000U;
            if (pulses == 0U)
            {
                pulses = 1U;
            }

            HAL_GPIO_WritePin(LED_PORT, LED_PIN, GPIO_PIN_SET);
            for (uint32_t i = 0U; i < pulses; i++)
            {
                if (StepOne(g_currentHz, 1U, 1U) == 0U)
                {
                    g_run = 0U;
                    break;
                }
                g_positionSteps += (g_dir == 0U) ? 1 : -1;
            }
            HAL_GPIO_WritePin(LED_PORT, LED_PIN, GPIO_PIN_RESET);
        }
        else
        {
            HAL_Delay(CONTROL_PERIOD_MS);
        }

        PollUart();
        PrintLiveTelemetry();
    }
}

static GPIO_PinState ActiveState(uint8_t on)
{
    if (SHIFTER_NON_INVERTED != 0U)
    {
        return (on != 0U) ? GPIO_PIN_SET : GPIO_PIN_RESET;
    }

    return (on != 0U) ? GPIO_PIN_RESET : GPIO_PIN_SET;
}

static uint8_t UpperLimitPressed(void)
{
    return (HAL_GPIO_ReadPin(UPPER_LIMIT_PORT, UPPER_LIMIT_PIN) == GPIO_PIN_RESET) ? 1U : 0U;
}

static uint8_t LowerLimitPressed(void)
{
    return (HAL_GPIO_ReadPin(LOWER_LIMIT_PORT, LOWER_LIMIT_PIN) == GPIO_PIN_RESET) ? 1U : 0U;
}

static void UpdateLimitDebounce(void)
{
    uint32_t now = HAL_GetTick();
    uint8_t upperRaw = UpperLimitPressed();
    uint8_t lowerRaw = LowerLimitPressed();

    if (g_limitDebounceInitialized == 0U)
    {
        g_upperLimitCandidate = upperRaw;
        g_lowerLimitCandidate = lowerRaw;
        g_upperLimitDebounced = upperRaw;
        g_lowerLimitDebounced = lowerRaw;
        g_upperLimitCandidateSinceMs = now;
        g_lowerLimitCandidateSinceMs = now;
        g_limitDebounceInitialized = 1U;
        return;
    }

    if (upperRaw != g_upperLimitCandidate)
    {
        g_upperLimitCandidate = upperRaw;
        g_upperLimitCandidateSinceMs = now;
    }
    else if (upperRaw != g_upperLimitDebounced &&
             (now - g_upperLimitCandidateSinceMs) >= LIMIT_DEBOUNCE_MS)
    {
        g_upperLimitDebounced = upperRaw;
    }

    if (lowerRaw != g_lowerLimitCandidate)
    {
        g_lowerLimitCandidate = lowerRaw;
        g_lowerLimitCandidateSinceMs = now;
    }
    else if (lowerRaw != g_lowerLimitDebounced &&
             (now - g_lowerLimitCandidateSinceMs) >= LIMIT_DEBOUNCE_MS)
    {
        g_lowerLimitDebounced = lowerRaw;
    }
}

static uint8_t UpperLimitPressedDebounced(void)
{
    UpdateLimitDebounce();
    return g_upperLimitDebounced;
}

static uint8_t LowerLimitPressedDebounced(void)
{
    UpdateLimitDebounce();
    return g_lowerLimitDebounced;
}

static uint8_t WaitForDebouncedLimit(uint8_t upperLimit)
{
    while ((upperLimit != 0U) ? (UpperLimitPressed() != 0U) : (LowerLimitPressed() != 0U))
    {
        ReportSwitchChanges();
        if ((upperLimit != 0U) ? (UpperLimitPressedDebounced() != 0U) :
                                 (LowerLimitPressedDebounced() != 0U))
        {
            return 1U;
        }
    }
    return 0U;
}

static HAL_StatusTypeDef AS5600_ReadAngle(uint16_t *angle)
{
    uint8_t data[2];

    if (g_i2cReady == 0U)
    {
        return HAL_ERROR;
    }

    if (HAL_GPIO_ReadPin(GPIOB, GPIO_PIN_8) == GPIO_PIN_RESET ||
        HAL_GPIO_ReadPin(GPIOB, GPIO_PIN_9) == GPIO_PIN_RESET)
    {
        return HAL_ERROR;
    }

    if (HAL_I2C_Mem_Read(&hi2c1, 0x36U << 1, 0x0CU, I2C_MEMADD_SIZE_8BIT,
                         data, sizeof(data), 3U) != HAL_OK)
    {
        return HAL_ERROR;
    }

    *angle = (uint16_t)((((uint16_t)data[0] << 8U) | data[1]) & 0x0FFFU);
    return HAL_OK;
}

static void SetDirection(uint8_t dir)
{
    g_dir = (dir != 0U) ? 1U : 0U;
    HAL_GPIO_WritePin(DIR_PORT, DIR_PIN, ActiveState(g_dir));
    HAL_Delay(5U);
}

static void DelayUs(uint32_t us)
{
    uint32_t start = DWT->CYCCNT;
    uint32_t ticks = (SystemCoreClock / 1000000U) * us;
    while ((DWT->CYCCNT - start) < ticks)
    {
    }
}

static uint8_t StepOne(uint32_t hz, uint8_t stopUpper, uint8_t stopLower)
{
    uint32_t halfUs;

    if (hz == 0U || (stopUpper != 0U && UpperLimitPressed() != 0U) ||
        (stopLower != 0U && LowerLimitPressed() != 0U))
    {
        return 0U;
    }

    halfUs = 500000U / hz;
    if (halfUs < 2U)
    {
        halfUs = 2U;
    }

    HAL_GPIO_WritePin(STEP_PORT, STEP_PIN, ActiveState(1U));
    DelayUs(halfUs);
    HAL_GPIO_WritePin(STEP_PORT, STEP_PIN, ActiveState(0U));
    DelayUs(halfUs);
    return 1U;
}

static void ClosedLoopTimerScheduleUs(uint32_t delayUs)
{
    if (delayUs < 2U)
    {
        delayUs = 2U;
    }

    TIM6->CR1 &= ~TIM_CR1_CEN;
    TIM6->ARR = delayUs - 1U;
    TIM6->CNT = 0U;
    TIM6->EGR = TIM_EGR_UG;
    TIM6->SR = 0U;
    TIM6->CR1 |= TIM_CR1_CEN;
    g_closedLoopTimerArmCount++;
}

static void ClosedLoopActuatorStop(void)
{
    uint32_t primask = __get_PRIMASK();
    __disable_irq();
    TIM6->CR1 &= ~TIM_CR1_CEN;
    TIM6->SR = 0U;
    HAL_NVIC_ClearPendingIRQ(TIM6_DAC_IRQn);
    g_closedLoopActuatorPhase = CLOSED_LOOP_PHASE_IDLE;
    HAL_GPIO_WritePin(STEP_PORT, STEP_PIN, ActiveState(0U));
    if (primask == 0U)
    {
        __enable_irq();
    }
}

static void ClosedLoopActuatorFault(uint8_t fault)
{
    g_parametricClosedLoopRunning = 0U;
    g_closedLoopFault = fault;
    g_closedLoopFaultPending = 1U;
    TIM6->CR1 &= ~TIM_CR1_CEN;
    TIM6->SR = 0U;
    g_closedLoopActuatorPhase = CLOSED_LOOP_PHASE_IDLE;
    HAL_GPIO_WritePin(STEP_PORT, STEP_PIN, ActiveState(0U));
}

static void ClosedLoopActuatorOnTimer(void)
{
    int32_t errorSteps;
    uint8_t desiredDirection;

    g_closedLoopActuatorCallbackCount++;

    if (g_parametricClosedLoopRunning == 0U)
    {
        ClosedLoopActuatorStop();
        return;
    }

    if (g_closedLoopActuatorPhase == CLOSED_LOOP_PHASE_STEP_HIGH)
    {
        HAL_GPIO_WritePin(STEP_PORT, STEP_PIN, ActiveState(0U));
        g_positionSteps += (g_dir == DIR_UP) ? 1 : -1;
        g_closedLoopStepCompleteCount++;
        g_closedLoopActuatorPhase = CLOSED_LOOP_PHASE_INTERSTEP;
        ClosedLoopTimerScheduleUs((1000000U / CLOSED_LOOP_MAX_STEP_HZ) - CLOSED_LOOP_STEP_PULSE_US);
        return;
    }

    if (g_closedLoopActuatorPhase == CLOSED_LOOP_PHASE_DIR_SETUP)
    {
        // The direction pin has now been stable for CLOSED_LOOP_DIR_SETUP_US;
        // only after this timer expiry may the STEP output rise.
        g_closedLoopActuatorPhase = CLOSED_LOOP_PHASE_IDLE;
    }
    else if (g_closedLoopActuatorPhase == CLOSED_LOOP_PHASE_INTERSTEP)
    {
        g_closedLoopActuatorPhase = CLOSED_LOOP_PHASE_IDLE;
    }

    errorSteps = g_parametricClosedLoopTargetSteps - g_positionSteps;
    if (errorSteps == 0)
    {
        g_closedLoopActuatorPhase = CLOSED_LOOP_PHASE_IDLE;
        return;
    }

    desiredDirection = (errorSteps > 0) ? DIR_UP : DIR_DOWN;
    if (desiredDirection == DIR_UP && UpperLimitPressed() != 0U)
    {
        ClosedLoopActuatorFault(1U);
        return;
    }
    if (desiredDirection == DIR_DOWN && LowerLimitPressed() != 0U)
    {
        ClosedLoopActuatorFault(2U);
        return;
    }

    if (g_dir != desiredDirection)
    {
        g_dir = desiredDirection;
        HAL_GPIO_WritePin(DIR_PORT, DIR_PIN, ActiveState(g_dir));
        g_closedLoopActuatorPhase = CLOSED_LOOP_PHASE_DIR_SETUP;
        ClosedLoopTimerScheduleUs(CLOSED_LOOP_DIR_SETUP_US);
        return;
    }

    HAL_GPIO_WritePin(STEP_PORT, STEP_PIN, ActiveState(1U));
    g_closedLoopStepHighCount++;
    g_closedLoopActuatorPhase = CLOSED_LOOP_PHASE_STEP_HIGH;
    ClosedLoopTimerScheduleUs(CLOSED_LOOP_STEP_PULSE_US);
}

static void ClosedLoopActuatorKick(void)
{
    uint32_t primask = __get_PRIMASK();

    g_closedLoopKickCount++;
    __disable_irq();
    if (g_parametricClosedLoopRunning != 0U &&
        g_closedLoopActuatorPhase == CLOSED_LOOP_PHASE_IDLE)
    {
        g_closedLoopActuatorPhase = CLOSED_LOOP_PHASE_INTERSTEP;
        ClosedLoopTimerScheduleUs(1U);
    }
    if (primask == 0U)
    {
        __enable_irq();
    }
}

static void ClosedLoopStepTimerInit(void)
{
    RCC_ClkInitTypeDef clockConfig;
    uint32_t flashLatency;
    uint32_t timerClockHz;

    __HAL_RCC_TIM6_CLK_ENABLE();
    HAL_RCC_GetClockConfig(&clockConfig, &flashLatency);
    timerClockHz = HAL_RCC_GetPCLK1Freq();
    if (clockConfig.APB1CLKDivider != RCC_HCLK_DIV1)
    {
        timerClockHz *= 2U;
    }

    TIM6->CR1 = TIM_CR1_OPM | TIM_CR1_URS;
    TIM6->PSC = (timerClockHz / 1000000U) - 1U;
    TIM6->ARR = 1U;
    TIM6->EGR = TIM_EGR_UG;
    TIM6->SR = 0U;
    TIM6->DIER = TIM_DIER_UIE;

    HAL_NVIC_SetPriority(TIM6_DAC_IRQn, 1U, 0U);
    HAL_NVIC_EnableIRQ(TIM6_DAC_IRQn);
}

static void Jog(uint8_t dir, uint32_t steps)
{
    g_parametricClosedLoopRunning = 0U;
    ClosedLoopActuatorStop();
    g_parametricTestRunning = 0U;
    g_sineRunning = 0U;
    g_parametricRunning = 0U;
    SetDirection(dir);
    for (uint32_t i = 0U; i < steps; i++)
    {
        if (StepOne(JOG_SPEED_HZ, 1U, 1U) == 0U)
        {
            UartPrint("jog stopped by limit\r\n");
            return;
        }
        g_positionSteps += (dir == 0U) ? 1 : -1;
        ReportSwitchChanges();
    }
    UartPrint("jog complete\r\n");
}

static void Home(void)
{
    uint32_t lowerSteps = 0U;
    uint32_t spanSteps = 0U;

    g_homed = 0U;
    g_parametricClosedLoopRunning = 0U;
    ClosedLoopActuatorStop();
    g_parametricTestRunning = 0U;
    g_sineRunning = 0U;
    g_parametricRunning = 0U;
    g_run = 0U;

    UartPrint("homing: moving to lower limit\r\n");
    SetDirection(DIR_DOWN);
    while (lowerSteps < MAX_HOMING_STEPS)
    {
        if (LowerLimitPressed() != 0U)
        {
            if (WaitForDebouncedLimit(0U) != 0U)
            {
                break;
            }
            continue;
        }
        if (StepOne(HOMING_SPEED_HZ, 0U, 1U) == 0U)
        {
            continue;
        }
        lowerSteps++;
        g_positionSteps += (DIR_DOWN == 0U) ? 1 : -1;
        ReportSwitchChanges();
    }

    if (LowerLimitPressedDebounced() == 0U)
    {
        UartPrint("homing failed: lower limit not found\r\n");
        return;
    }

    UartPrint("homing: lower limit found; reversing\r\n");

    UartPrint("homing: moving to upper limit\r\n");
    SetDirection(DIR_UP);
    while (spanSteps < MAX_HOMING_STEPS)
    {
        if (UpperLimitPressed() != 0U)
        {
            if (WaitForDebouncedLimit(1U) != 0U)
            {
                break;
            }
            continue;
        }
        if (StepOne(HOMING_SPEED_HZ, 1U, 0U) == 0U)
        {
            continue;
        }
        spanSteps++;
        g_positionSteps += (DIR_UP == 0U) ? 1 : -1;
        ReportSwitchChanges();
    }

    if (UpperLimitPressedDebounced() == 0U)
    {
        UartPrint("homing failed: upper limit not found\r\n");
        return;
    }

    {
        float travelMm = (float)spanSteps / g_stepsPerMm;
        char calibrationMessage[120];
        snprintf(calibrationMessage, sizeof(calibrationMessage),
                 "homing calibration: span=%lu steps scale=%.3f steps/mm travel=%.3f mm\r\n",
                 (unsigned long)spanSteps,
                 (double)g_stepsPerMm,
                 (double)travelMm);
        UartPrint(calibrationMessage);
    }
    UartPrint("homing: moving to midpoint\r\n");
    SetDirection(DIR_DOWN);
    for (uint32_t i = 0U; i < spanSteps / 2U; i++)
    {
        if (StepOne(HOMING_SPEED_HZ, 0U, 1U) == 0U)
        {
            UartPrint("homing failed: unexpected lower limit during midpoint move\r\n");
            return;
        }
        g_positionSteps += (DIR_DOWN == 0U) ? 1 : -1;
        ReportSwitchChanges();
    }

    g_travelSteps = spanSteps;
    g_positionSteps = 0;
    g_homed = 1U;
    UartPrint("homing complete: midpoint reached\r\n");
}

static void ReturnToCenter(void)
{
    UartPrint("sine stopped: returning pivot to zero\r\n");
    while (g_positionSteps != 0)
    {
        uint8_t direction = (g_positionSteps < 0) ? 0U : 1U;
        SetDirection(direction);
        if (StepOne(JOG_SPEED_HZ, 1U, 1U) == 0U)
        {
            UartPrint("return to zero stopped by limit\r\n");
            return;
        }
        g_positionSteps += (direction == 0U) ? 1 : -1;
        ReportSwitchChanges();
        PrintLiveTelemetry();
    }
    UartPrint("pivot returned to zero\r\n");
}

static void UpdateSine(void)
{
    uint32_t elapsedMs = HAL_GetTick() - g_sineStartMs;
    float elapsedSeconds = (float)elapsedMs / 1000.0f;
    float phase = 2.0f * PI_F * g_sineFrequencyHz * elapsedSeconds;
    float targetMm = g_sineAmplitudeMm * sinf(phase);
    int32_t targetSteps = (int32_t)lroundf(targetMm * g_stepsPerMm);

    while (g_positionSteps != targetSteps)
    {
        uint8_t direction = (targetSteps > g_positionSteps) ? 0U : 1U;
        float amplitudeSteps = g_sineAmplitudeMm * g_stepsPerMm;
        uint32_t speedHz = (uint32_t)(2.0f * PI_F * g_sineFrequencyHz *
                                      amplitudeSteps) + 20U;

        if (speedHz < JOG_SPEED_HZ)
        {
            speedHz = JOG_SPEED_HZ;
        }
        if (speedHz > 8000U)
        {
            speedHz = 8000U;
        }

        if (g_dir != direction)
        {
            SetDirection(direction);
        }
        if (StepOne(speedHz, 1U, 1U) == 0U)
        {
            g_sineRunning = 0U;
            g_run = 0U;
            UartPrint("sine stopped: limit switch reached\r\n");
            return;
        }

        g_positionSteps += (direction == 0U) ? 1 : -1;
    }
}

static void UpdateParametric(void)
{
    uint16_t angle;
    float rawThetaRad;
    float dt;
    float alpha;
    float thetaSq;
    float acSignal;
    float derivative;
    float cos2;
    float sin2;
    float targetMm;
    float maxStepMm;
    int32_t targetSteps;
    int32_t halfTravelSteps;
    uint32_t now = HAL_GetTick();

    if ((now - g_parametricLastUpdateMs) < PARAMETRIC_UPDATE_PERIOD_MS)
    {
        return;
    }

    if (AS5600_ReadAngle(&angle) != HAL_OK)
    {
        g_parametricLastUpdateMs = now;
        return;
    }

    rawThetaRad = RelativeAngleDegrees(angle) * (PI_F / 180.0f);
    dt = (g_parametricFilterValid == 0U) ? 0.0f : (float)(now - g_parametricLastUpdateMs) / 1000.0f;
    g_parametricLastUpdateMs = now;

    if (g_parametricFilterValid == 0U || dt <= 0.0f)
    {
        // Seed every filter stage from the first sample; the derivative needs a
        // second sample before it means anything, so skip stepping this call.
        g_parametricFilteredThetaRad = rawThetaRad;
        g_parametricThetaSqDc = rawThetaRad * rawThetaRad;
        g_parametricPrevAcSignal = 0.0f;
        g_parametricCosPeak = 0.0f;
        g_parametricSinPeak = 0.0f;
        g_parametricFilterValid = 1U;
        return;
    }

    // Low-pass the noisy/quantized angle before squaring it.
    alpha = dt / (dt + PARAMETRIC_ANGLE_TIME_CONST_S);
    g_parametricFilteredThetaRad += alpha * (rawThetaRad - g_parametricFilteredThetaRad);

    // theta^2 = 0.5*Theta0^2*(1 - cos(2*phase)): squaring the angle (not doubling
    // it inside sin/cos) is what yields an EXACT, harmonic-free 2x-frequency
    // term regardless of swing amplitude -- this mirrors the Simulink chain.
    thetaSq = g_parametricFilteredThetaRad * g_parametricFilteredThetaRad;

    {
        float dcAlpha = dt / (dt + PARAMETRIC_DC_BLOCK_TIME_CONST_S);
        g_parametricThetaSqDc += dcAlpha * (thetaSq - g_parametricThetaSqDc);
    }
    acSignal = thetaSq - g_parametricThetaSqDc; // proportional to -cos(2*phase)

    derivative = (acSignal - g_parametricPrevAcSignal) / dt; // proportional to sin(2*phase)
    g_parametricPrevAcSignal = acSignal;

    {
        float agcAlpha = dt / (dt + PARAMETRIC_AGC_TIME_CONST_S);
        float decayedCosPeak = g_parametricCosPeak * (1.0f - agcAlpha);
        float decayedSinPeak = g_parametricSinPeak * (1.0f - agcAlpha);
        float absCos = fabsf(acSignal);
        float absSin = fabsf(derivative);
        g_parametricCosPeak = (absCos > decayedCosPeak) ? absCos : decayedCosPeak;
        g_parametricSinPeak = (absSin > decayedSinPeak) ? absSin : decayedSinPeak;
    }

    // Two independent AGCs normalize out Theta0^2 so amplitude only depends on
    // the commanded pivot amplitude, not on how wide the pendulum happens to swing.
    cos2 = (g_parametricCosPeak > PARAMETRIC_AGC_MIN_PEAK) ? (-acSignal / g_parametricCosPeak) : 0.0f;
    sin2 = (g_parametricSinPeak > PARAMETRIC_AGC_MIN_PEAK) ? (derivative / g_parametricSinPeak) : 0.0f;

    targetMm = g_parametricAmplitudeMm *
               (sin2 * cosf(g_parametricPhiRad) + cos2 * sinf(g_parametricPhiRad));

    // Slew-limit the commanded position so residual noise ramps instead of jerking.
    maxStepMm = PARAMETRIC_SLEW_MM_PER_SEC * dt;
    if ((targetMm - g_parametricCommandedMm) > maxStepMm)
    {
        targetMm = g_parametricCommandedMm + maxStepMm;
    }
    else if ((targetMm - g_parametricCommandedMm) < -maxStepMm)
    {
        targetMm = g_parametricCommandedMm - maxStepMm;
    }
    g_parametricCommandedMm = targetMm;

    halfTravelSteps = (int32_t)(g_travelSteps / 2U);
    targetSteps = (int32_t)(targetMm * g_stepsPerMm);
    if (targetSteps > halfTravelSteps)
    {
        targetSteps = halfTravelSteps;
    }
    else if (targetSteps < -halfTravelSteps)
    {
        targetSteps = -halfTravelSteps;
    }

    while (g_positionSteps != targetSteps)
    {
        uint8_t direction = (targetSteps > g_positionSteps) ? 0U : 1U;

        if (g_dir != direction)
        {
            SetDirection(direction);
        }
        if (StepOne(PARAMETRIC_SPEED_HZ, 1U, 1U) == 0U)
        {
            g_parametricRunning = 0U;
            g_run = 0U;
            UartPrint("parametric stopped: limit switch reached\r\n");
            return;
        }
        g_positionSteps += (direction == 0U) ? 1 : -1;
    }
}

static float ParametricTestDcBlockApprox(float input, float dt, uint8_t reset)
{
    if (reset != 0U)
    {
        g_parametricTestDcEstimate = input;
        return 0.0f;
    }

    // Temporary approximation only. Replace this first-order DC estimate with
    // the exact sixth-order Simulink IIR DC blocker when its coefficients are available.
    {
        float alpha = dt / (dt + PARAMETRIC_DC_BLOCK_TIME_CONST_S);
        g_parametricTestDcEstimate += alpha * (input - g_parametricTestDcEstimate);
    }
    return input - g_parametricTestDcEstimate;
}

static float ParametricTestAgc(float input, float *logGain)
{
    float previousLogGain = *logGain;
    float output = input * expf(previousLogGain);
    float detector = input * input;
    float z = detector * expf(2.0f * previousLogGain);
    float zSafe = (z > PARAMETRIC_TEST_AGC_Z_EPSILON) ? z : PARAMETRIC_TEST_AGC_Z_EPSILON;
    float updatedLogGain = previousLogGain - PARAMETRIC_TEST_AGC_STEP * logf(zSafe);

    // A 60 dB power-gain limit is an amplitude gain of 10^(60/20) = 1000,
    // so the logarithmic gain state is limited to ln(1000).
    if (updatedLogGain > PARAMETRIC_TEST_AGC_MAX_LOG_GAIN)
    {
        updatedLogGain = PARAMETRIC_TEST_AGC_MAX_LOG_GAIN;
    }
    *logGain = updatedLogGain;
    return output;
}

static void UpdateParametricTest(void)
{
    uint16_t angle;
    uint32_t now = HAL_GetTick();
    uint32_t elapsedMs = now - g_parametricTestLastSampleMs;
    float thetaDeg;
    float thetaRad;
    float thetaSq;
    float dcBlocked;
    float directAgc;
    float quadAgc;
    float directSat;
    float quadSat;
    float combined;
    float outputSat;
    float masterOut;
    float rateDelta;
    float zCmdMm;
    int32_t targetSteps;
    float dt;
    char msg[180];

    if (elapsedMs < PARAMETRIC_TEST_PERIOD_MS)
    {
        return;
    }
    g_parametricTestLastSampleMs = now;

    if (AS5600_ReadAngle(&angle) != HAL_OK)
    {
        return;
    }

    thetaDeg = RelativeAngleDegrees(angle);
    thetaRad = thetaDeg * (PI_F / 180.0f);
    thetaSq = thetaRad * thetaRad;
    dt = (float)elapsedMs / 1000.0f;

    dcBlocked = ParametricTestDcBlockApprox(thetaSq, dt, (g_parametricTestFilterValid == 0U) ? 1U : 0U);
    if (g_parametricTestFilterValid == 0U)
    {
        g_parametricTestPrevDcBlocked = dcBlocked;
        g_parametricTestQuadFiltered = 0.0f;
        g_parametricTestFilterValid = 1U;
    }
    else
    {
        // Simulink Discrete Transfer Function: [20 -20] / [1 -0.9048].
        g_parametricTestQuadFiltered = 0.9048f * g_parametricTestQuadFiltered +
                                        20.0f * dcBlocked -
                                        20.0f * g_parametricTestPrevDcBlocked;
        g_parametricTestPrevDcBlocked = dcBlocked;
    }

    directAgc = ParametricTestAgc(dcBlocked, &g_parametricTestDirectAgcLogGain);
    quadAgc = ParametricTestAgc(g_parametricTestQuadFiltered, &g_parametricTestQuadAgcLogGain);
    directSat = (directAgc > PARAMETRIC_TEST_SATURATION) ? PARAMETRIC_TEST_SATURATION :
                ((directAgc < -PARAMETRIC_TEST_SATURATION) ? -PARAMETRIC_TEST_SATURATION : directAgc);
    quadSat = (quadAgc > PARAMETRIC_TEST_SATURATION) ? PARAMETRIC_TEST_SATURATION :
              ((quadAgc < -PARAMETRIC_TEST_SATURATION) ? -PARAMETRIC_TEST_SATURATION : quadAgc);
    combined = quadSat * PARAMETRIC_TEST_PHASE_COS + directSat * PARAMETRIC_TEST_PHASE_SIN;

    // The Simulink 10 us Transport Delay is intentionally omitted because it is
    // only 0.2% of the 5 ms controller sample and a discrete delay would add 5 ms.

    // Simulink Saturation2: [-2, +2]. This is separate from the branch saturations.
    outputSat = (combined > PARAMETRIC_TEST_OUTPUT_SATURATION) ? PARAMETRIC_TEST_OUTPUT_SATURATION :
                ((combined < -PARAMETRIC_TEST_OUTPUT_SATURATION) ? -PARAMETRIC_TEST_OUTPUT_SATURATION : combined);

    // Simulink Master Gain: 2. The resulting pinion angle command is in radians.
    masterOut = PARAMETRIC_TEST_MASTER_GAIN * outputSat;

    // Simulink Rate Limiter: +/-20 rad/s at Ts=5 ms gives +/-0.1 rad/sample.
    rateDelta = masterOut - g_parametricTestRateLimitedRad;
    if (rateDelta > PARAMETRIC_TEST_RATE_DELTA_RAD)
    {
        rateDelta = PARAMETRIC_TEST_RATE_DELTA_RAD;
    }
    else if (rateDelta < -PARAMETRIC_TEST_RATE_DELTA_RAD)
    {
        rateDelta = -PARAMETRIC_TEST_RATE_DELTA_RAD;
    }
    g_parametricTestRateLimitedRad += rateDelta;

    // Simscape pinion radius: 10 mm. Physical setup calibration: 40 steps/mm.
    // Both values below are diagnostic only and are never sent to a motor path.
    zCmdMm = PARAMETRIC_TEST_PINION_RADIUS_MM * g_parametricTestRateLimitedRad;
    targetSteps = (int32_t)lroundf(zCmdMm * PARAMETRIC_TEST_STEPS_PER_MM);

    if ((g_parametricTestSampleCount++ % PARAMETRIC_TEST_TELEMETRY_DECIMATION) != 0U)
    {
        return;
    }

    snprintf(msg, sizeof(msg), "%.3f,%.2f,%.5f,%.5f,%.5f,%.5f,%.3f,%ld\r\n",
             (double)(now - g_parametricTestStartMs) / 1000.0,
             (double)thetaDeg,
             (double)combined,
             (double)outputSat,
             (double)masterOut,
             (double)g_parametricTestRateLimitedRad,
             (double)zCmdMm,
             (long)targetSteps);
    UartPrint(msg);
}

static void UpdateParametricClosedLoop(void)
{
    uint16_t angle;
    uint32_t now = HAL_GetTick();
    float thetaDeg;
    float thetaRad;
    float thetaSq;
    float dcBlocked;
    float directAgc;
    float quadAgc;
    float directSat;
    float quadSat;
    float combined;
    float outputSat;
    float masterOut;
    float rateDelta;
    float zSimscapeMm;
    float zMotorTargetMm;
    const float dt = (float)PARAMETRIC_TEST_PERIOD_MS / 1000.0f;
    int32_t targetSteps;
    int32_t allowedHalfTravelSteps;
    int32_t travelMarginSteps;
    int32_t actualSteps;
    char msg[180];

    if ((int32_t)(now - g_parametricClosedLoopNextSampleMs) < 0)
    {
        return;
    }

    // The validated discrete filter coefficients and +/-0.1 rad rate limit
    // correspond to Ts=5 ms. Advance the fixed schedule to avoid accumulated
    // drift, skipping missed ticks rather than changing the discrete equations.
    do
    {
        g_parametricClosedLoopNextSampleMs += PARAMETRIC_TEST_PERIOD_MS;
    } while ((int32_t)(now - g_parametricClosedLoopNextSampleMs) >= 0);

    if (AS5600_ReadAngle(&angle) != HAL_OK)
    {
        g_parametricClosedLoopAngleFailureCount++;
        if (g_parametricClosedLoopAngleFailureCount >= CLOSED_LOOP_ANGLE_FAILURE_LIMIT)
        {
            ClosedLoopActuatorFault(3U);
        }
        return;
    }
    g_parametricClosedLoopAngleFailureCount = 0U;

    thetaDeg = RelativeAngleDegrees(angle);
    thetaRad = thetaDeg * (PI_F / 180.0f);
    thetaSq = thetaRad * thetaRad;
    if (g_parametricClosedLoopFilterValid == 0U)
    {
        g_parametricClosedLoopDcEstimate = thetaSq;
        dcBlocked = 0.0f;
        g_parametricClosedLoopPrevDcBlocked = 0.0f;
        g_parametricClosedLoopQuadFiltered = 0.0f;
        g_parametricClosedLoopFilterValid = 1U;
    }
    else
    {
        float dcAlpha = dt / (dt + PARAMETRIC_DC_BLOCK_TIME_CONST_S);
        g_parametricClosedLoopDcEstimate += dcAlpha * (thetaSq - g_parametricClosedLoopDcEstimate);
        dcBlocked = thetaSq - g_parametricClosedLoopDcEstimate;
        g_parametricClosedLoopQuadFiltered = 0.9048f * g_parametricClosedLoopQuadFiltered +
                                              20.0f * dcBlocked -
                                              20.0f * g_parametricClosedLoopPrevDcBlocked;
        g_parametricClosedLoopPrevDcBlocked = dcBlocked;
    }

    directAgc = ParametricTestAgc(dcBlocked, &g_parametricClosedLoopDirectAgcLogGain);
    quadAgc = ParametricTestAgc(g_parametricClosedLoopQuadFiltered,
                               &g_parametricClosedLoopQuadAgcLogGain);
    directSat = (directAgc > PARAMETRIC_TEST_SATURATION) ? PARAMETRIC_TEST_SATURATION :
                ((directAgc < -PARAMETRIC_TEST_SATURATION) ? -PARAMETRIC_TEST_SATURATION : directAgc);
    quadSat = (quadAgc > PARAMETRIC_TEST_SATURATION) ? PARAMETRIC_TEST_SATURATION :
              ((quadAgc < -PARAMETRIC_TEST_SATURATION) ? -PARAMETRIC_TEST_SATURATION : quadAgc);
    combined = quadSat * PARAMETRIC_TEST_PHASE_COS + directSat * PARAMETRIC_TEST_PHASE_SIN;
    outputSat = (combined > PARAMETRIC_TEST_OUTPUT_SATURATION) ? PARAMETRIC_TEST_OUTPUT_SATURATION :
                ((combined < -PARAMETRIC_TEST_OUTPUT_SATURATION) ? -PARAMETRIC_TEST_OUTPUT_SATURATION : combined);
    masterOut = PARAMETRIC_TEST_MASTER_GAIN * outputSat;

    rateDelta = masterOut - g_parametricClosedLoopRateLimitedRad;
    if (rateDelta > PARAMETRIC_TEST_RATE_DELTA_RAD)
    {
        rateDelta = PARAMETRIC_TEST_RATE_DELTA_RAD;
    }
    else if (rateDelta < -PARAMETRIC_TEST_RATE_DELTA_RAD)
    {
        rateDelta = -PARAMETRIC_TEST_RATE_DELTA_RAD;
    }
    g_parametricClosedLoopRateLimitedRad += rateDelta;

    // Original Simscape-equivalent rack command from the 10 mm pinion radius.
    zSimscapeMm = PARAMETRIC_TEST_PINION_RADIUS_MM * g_parametricClosedLoopRateLimitedRad;
    // Experimental physical-command polarity: +1 original, -1 180-degree inversion.
    zMotorTargetMm = CLOSED_LOOP_COMMAND_SIGN * zSimscapeMm;
    // Stage-4 physical command is intentionally restricted to +/-7.5 mm.
    zMotorTargetMm = (zMotorTargetMm > CLOSED_LOOP_MAX_MM) ? CLOSED_LOOP_MAX_MM :
                     ((zMotorTargetMm < -CLOSED_LOOP_MAX_MM) ? -CLOSED_LOOP_MAX_MM : zMotorTargetMm);
    targetSteps = (int32_t)lroundf(zMotorTargetMm * g_stepsPerMm);

    travelMarginSteps = (int32_t)lroundf(CLOSED_LOOP_TRAVEL_MARGIN_MM * g_stepsPerMm);
    allowedHalfTravelSteps = (int32_t)(g_travelSteps / 2U) - travelMarginSteps;
    if (allowedHalfTravelSteps < 0)
    {
        allowedHalfTravelSteps = 0;
    }
    if (targetSteps > allowedHalfTravelSteps)
    {
        targetSteps = allowedHalfTravelSteps;
    }
    else if (targetSteps < -allowedHalfTravelSteps)
    {
        targetSteps = -allowedHalfTravelSteps;
    }

    g_parametricClosedLoopTargetSteps = targetSteps;
    ClosedLoopActuatorKick();

    if ((g_parametricClosedLoopSampleCount++ % PARAMETRIC_TEST_TELEMETRY_DECIMATION) != 0U)
    {
        return;
    }

    actualSteps = g_positionSteps;
    snprintf(msg, sizeof(msg), "%.3f,%.2f,%.3f,%.3f,%.3f,%ld,%ld,%ld\r\n",
             (double)(now - g_parametricClosedLoopStartMs) / 1000.0,
             (double)thetaDeg,
             (double)zSimscapeMm,
             (double)((float)targetSteps / g_stepsPerMm),
             (double)((float)actualSteps / g_stepsPerMm),
             (long)targetSteps,
             (long)actualSteps,
             (long)(targetSteps - actualSteps));
    UartPrint(msg);
}

static void ReportClosedLoopFault(void)
{
    uint8_t fault;

    if (g_closedLoopFaultPending == 0U)
    {
        return;
    }

    __disable_irq();
    fault = g_closedLoopFault;
    g_closedLoopFaultPending = 0U;
    __enable_irq();

    if (fault == 1U)
    {
        UartPrint("closed loop stopped: upper limit reached\r\n");
    }
    else if (fault == 2U)
    {
        UartPrint("closed loop stopped: lower limit reached\r\n");
    }
    else if (fault == 3U)
    {
        UartPrint("closed loop stopped: angle sensor unavailable\r\n");
    }
}

static void UartPrint(const char *text)
{
    size_t length = strlen(text);

    // Enqueue into the TX ring and let the ISR drain it, so printing never
    // blocks the step-timing loop for the several milliseconds a full
    // telemetry line takes to shift out at 115200 baud.
    __disable_irq();
    for (size_t i = 0U; i < length; i++)
    {
        uint16_t nextHead = (uint16_t)((g_uartTxHead + 1U) % UART_TX_RING_SIZE);
        if (nextHead == g_uartTxTail)
        {
            break;
        }
        g_uartTxRing[g_uartTxHead] = (uint8_t)text[i];
        g_uartTxHead = nextHead;
    }
    __enable_irq();
    UartTxKick();
}

static void PrintHelp(void)
{
    UartPrint("Commands:\r\n");
    UartPrint("  help            - show commands\r\n");
    UartPrint("  status          - print current settings\r\n");
    UartPrint("  switches        - show upper/lower switch states\r\n");
    UartPrint("  angle           - read AS5600 raw angle (0..4095)\r\n");
    UartPrint("  zero            - set current pendulum angle to 0 degrees\r\n");
    UartPrint("  live 0|1        - stop/start angle and position stream\r\n");
    UartPrint("  record 0|1      - stop/start CSV recording\r\n");
    UartPrint("  scale <steps/mm> - set pivot position conversion\r\n");
    UartPrint("  stop            - stop live output or recording\r\n");
    UartPrint("  jog <dir> <steps> - move a bounded number of steps\r\n");
    UartPrint("  home            - find both limits and move to midpoint\r\n");
    UartPrint("  sine <amp_mm> <hz> - start sinusoidal pivot motion after homing\r\n");
    UartPrint("  sine stop       - stop sine motion\r\n");
    UartPrint("  parametric <amp_mm> <phi_deg> - drive pivot at 2x pendulum phase rate\r\n");
    UartPrint("  parametric stop - stop parametric drive\r\n");
    UartPrint("  parametric_test 0|1 - test parametric signal processing without motor motion\r\n");
    UartPrint("  parametric_closed_loop 0|1 - run bounded parametric motor control\r\n");
    UartPrint("  run 0|1         - disable/enable stepping\r\n");
    UartPrint("  dir 0|1         - set direction\r\n");
    UartPrint("  hz <value>      - target speed in steps/sec\r\n");
    UartPrint("  accel <value>   - acceleration in steps/sec^2\r\n");
}

static void PrintStatus(void)
{
    char msg[300];
    char timerMsg[300];
    uint16_t angle;

    UpdateLimitDebounce();

    if (AS5600_ReadAngle(&angle) == HAL_OK)
    {
        snprintf(msg, sizeof(msg), "angle=%u/4095 ", (unsigned int)angle);
        UartPrint(msg);
    }
    else
    {
        UartPrint("angle=unavailable ");
    }

    snprintf(
        msg,
        sizeof(msg),
        "status: run=%u homed=%u sine=%u parametric=%u parametric_test=%u closed_loop=%u fault=%u upper=%u lower=%u span=%lu position=%ld target=%ld scale=%.3f\r\n",
        (unsigned int)g_run,
        (unsigned int)g_homed,
        (unsigned int)g_sineRunning,
        (unsigned int)g_parametricRunning,
        (unsigned int)g_parametricTestRunning,
        (unsigned int)g_parametricClosedLoopRunning,
        (unsigned int)g_closedLoopFault,
        (unsigned int)g_upperLimitDebounced,
        (unsigned int)g_lowerLimitDebounced,
        (unsigned long)g_travelSteps,
        (long)g_positionSteps,
        (long)g_parametricClosedLoopTargetSteps,
        (double)g_stepsPerMm);
    UartPrint(msg);

    snprintf(
        timerMsg,
        sizeof(timerMsg),
        "closed_loop_diag: command_sign=%.0f phase=%u kick=%lu arm=%lu irq=%lu callback=%lu step_high=%lu step_complete=%lu TIM6_CR1=0x%08lX DIER=0x%08lX SR=0x%08lX CNT=%lu PSC=%lu ARR=%lu\r\n",
        (double)CLOSED_LOOP_COMMAND_SIGN,
        (unsigned int)g_closedLoopActuatorPhase,
        (unsigned long)g_closedLoopKickCount,
        (unsigned long)g_closedLoopTimerArmCount,
        (unsigned long)g_tim6IrqCount,
        (unsigned long)g_closedLoopActuatorCallbackCount,
        (unsigned long)g_closedLoopStepHighCount,
        (unsigned long)g_closedLoopStepCompleteCount,
        (unsigned long)TIM6->CR1,
        (unsigned long)TIM6->DIER,
        (unsigned long)TIM6->SR,
        (unsigned long)TIM6->CNT,
        (unsigned long)TIM6->PSC,
        (unsigned long)TIM6->ARR);
    UartPrint(timerMsg);
}

static void PrintAngle(void)
{
    uint16_t angle;
    char msg[80];

    if (AS5600_ReadAngle(&angle) != HAL_OK)
    {
        UartPrint("angle read failed: check AS5600 power, pull-ups, and D14/D15 wiring\r\n");
        return;
    }

    snprintf(msg, sizeof(msg), "angle: raw=%u/4095 degrees=%.2f\r\n",
             (unsigned int)angle, (double)RelativeAngleDegrees(angle));
    UartPrint(msg);
}

static void SetAngleZero(void)
{
    uint16_t angle;

    if (AS5600_ReadAngle(&angle) == HAL_OK)
    {
        g_angleZeroRaw = angle;
        g_angleZeroValid = 1U;
        UartPrint("angle zero set\r\n");
    }
    else
    {
        g_angleZeroValid = 0U;
        UartPrint("angle zero unavailable\r\n");
    }
}

static float RelativeAngleDegrees(uint16_t rawAngle)
{
    int32_t relativeRaw = (int32_t)rawAngle - (int32_t)g_angleZeroRaw;

    if (relativeRaw > 2048)
    {
        relativeRaw -= 4096;
    }
    else if (relativeRaw < -2048)
    {
        relativeRaw += 4096;
    }

    return (float)relativeRaw * 360.0f / 4096.0f;
}

static void PrintLiveTelemetry(void)
{
    char msg[140];
    uint16_t angle;
    uint32_t now;

    if (g_liveTelemetry == 0U && g_recording == 0U)
    {
        return;
    }

    now = HAL_GetTick();
    if ((now - g_lastLiveMs) < LIVE_PERIOD_MS)
    {
        return;
    }
    g_lastLiveMs = now;

    if (AS5600_ReadAngle(&angle) == HAL_OK)
    {
        if (g_recording != 0U)
        {
            snprintf(msg, sizeof(msg), "%.3f,%.3f,%.3f\r\n",
                     (double)((float)(now - g_recordStartMs) / 1000.0f),
                     (double)((float)g_positionSteps / g_stepsPerMm),
                     (double)RelativeAngleDegrees(angle));
        }
        else
        {
            snprintf(msg, sizeof(msg), "live time=%.3f angle_deg=%.2f position_mm=%.3f position_steps=%ld homed=%u\r\n",
                     (double)((float)now / 1000.0f),
                     (double)RelativeAngleDegrees(angle),
                     (double)((float)g_positionSteps / g_stepsPerMm),
                     (long)g_positionSteps,
                     (unsigned int)g_homed);
        }
    }
    else
    {
        if (g_recording != 0U)
        {
            snprintf(msg, sizeof(msg), "%.3f,NaN,NaN\r\n",
                     (double)((float)now / 1000.0f));
        }
        else
        {
            snprintf(msg, sizeof(msg), "live time=%.3f angle=unavailable position_mm=%.3f position_steps=%ld homed=%u\r\n",
                     (double)((float)now / 1000.0f),
                     (double)((float)g_positionSteps / g_stepsPerMm),
                     (long)g_positionSteps,
                     (unsigned int)g_homed);
        }
    }
    UartPrint(msg);
}

static void PrintSwitches(void)
{
    char msg[80];
    UpdateLimitDebounce();
    snprintf(
        msg,
        sizeof(msg),
        "switches: upper=%u lower=%u\r\n",
        (unsigned int)g_upperLimitDebounced,
        (unsigned int)g_lowerLimitDebounced);
    UartPrint(msg);
}

static void ReportSwitchChanges(void)
{
    uint8_t upperLimit;
    uint8_t lowerLimit;

    UpdateLimitDebounce();
    upperLimit = g_upperLimitDebounced;
    lowerLimit = g_lowerLimitDebounced;

    if (g_switchStatusValid == 0U || upperLimit != g_lastUpperLimit ||
        lowerLimit != g_lastLowerLimit)
    {
        g_lastUpperLimit = upperLimit;
        g_lastLowerLimit = lowerLimit;
        g_switchStatusValid = 1U;
        PrintSwitches();
    }
}

static void ProcessLine(char *line)
{
    uint32_t value;
    size_t length = strlen(line);
    size_t start = 0U;

    while (start < length && (line[start] == ' ' || line[start] == '\t'))
    {
        start++;
    }

    if (start != 0U)
    {
        memmove(line, line + start, length - start + 1U);
        length -= start;
    }

    while (length > 0U && (line[length - 1U] == ' ' || line[length - 1U] == '\t'))
    {
        line[--length] = '\0';
    }

    if (length == 0U)
    {
        return;
    }

    // TEMPORARY UART/PARSER DEBUG
    if (g_parametricTestRunning == 0U && g_parametricClosedLoopRunning == 0U)
    {
        char debugMsg[200];
        size_t offset = 0U;
        offset += snprintf(debugMsg + offset, sizeof(debugMsg) - offset,
                           "RX DEBUG len=%lu bytes=",
                           (unsigned long)length);
        for (size_t i = 0U; i < length; i++)
        {
            offset += snprintf(debugMsg + offset, sizeof(debugMsg) - offset,
                               "%s%02X",
                               (i == 0U) ? "" : " ",
                               (unsigned int)((unsigned char)line[i]));
        }
        offset += snprintf(debugMsg + offset, sizeof(debugMsg) - offset, "\r\n");
        UartPrint(debugMsg);
    }

    for (size_t i = 0U; i < length; i++)
    {
        if (line[i] >= 'A' && line[i] <= 'Z')
        {
            line[i] = (char)(line[i] - 'A' + 'a');
        }
    }

    if (strcmp(line, "help") == 0)
    {
        PrintHelp();
        return;
    }

    if (strcmp(line, "status") == 0)
    {
        PrintStatus();
        return;
    }

    if (strcmp(line, "angle") == 0)
    {
        PrintAngle();
        return;
    }

    if (strcmp(line, "zero") == 0)
    {
        SetAngleZero();
        return;
    }

    if (strncmp(line, "live ", 5) == 0)
    {
        value = strtoul(line + 5, NULL, 10);
        if (value > 1U)
        {
            UartPrint("usage: live 0|1\r\n");
            return;
        }
        g_liveTelemetry = (uint8_t)value;
        g_recording = 0U;
        g_lastLiveMs = HAL_GetTick();
        UartPrint(g_liveTelemetry != 0U ? "live telemetry enabled\r\n" : "live telemetry disabled\r\n");
        return;
    }

    if (strncmp(line, "record ", 7) == 0)
    {
        value = strtoul(line + 7, NULL, 10);
        if (value > 1U)
        {
            UartPrint("usage: record 0|1\r\n");
            return;
        }
        g_liveTelemetry = (uint8_t)value;
        g_recording = (uint8_t)value;
        g_lastLiveMs = HAL_GetTick();
        if (g_recording != 0U)
        {
            g_recordStartMs = g_lastLiveMs;
            UartPrint("time_sec,position_mm,angle_deg\r\n");
        }
        else
        {
            g_suppressPromptOnce = 1U;
        }
        return;
    }

    if (strcmp(line, "stop") == 0)
    {
        g_parametricClosedLoopRunning = 0U;
        ClosedLoopActuatorStop();
        g_sineRunning = 0U;
        g_parametricRunning = 0U;
        g_parametricTestRunning = 0U;
        g_run = 0U;
        g_liveTelemetry = 0U;
        g_recording = 0U;
        UartPrint("stopped: motion and telemetry halted\r\n");
        return;
    }

    if (strncmp(line, "parametric_test", 15) == 0 &&
        (line[15] == '\0' || line[15] == ' ' || line[15] == '\t'))
    {
        char *argument = line + 15;
        while (*argument == ' ' || *argument == '\t')
        {
            argument++;
        }

        if (strcmp(argument, "1") == 0)
        {
            uint32_t now = HAL_GetTick();
            g_parametricClosedLoopRunning = 0U;
            ClosedLoopActuatorStop();
            g_sineRunning = 0U;
            g_parametricRunning = 0U;
            g_run = 0U;
            g_liveTelemetry = 0U;
            g_recording = 0U;
            g_parametricTestFilterValid = 0U;
            g_parametricTestDcEstimate = 0.0f;
            g_parametricTestPrevDcBlocked = 0.0f;
            g_parametricTestQuadFiltered = 0.0f;
            g_parametricTestDirectAgcLogGain = 0.0f;
            g_parametricTestQuadAgcLogGain = 0.0f;
            g_parametricTestRateLimitedRad = 0.0f;
            g_parametricTestSampleCount = 0U;
            g_parametricTestStartMs = now;
            g_parametricTestLastSampleMs = now - PARAMETRIC_TEST_PERIOD_MS;
            g_parametricTestRunning = 1U;
            g_suppressPromptOnce = 1U;
            UartPrint("time_s,theta_deg,combined,sat2,master_gain,rate_limited,z_cmd_mm,target_steps\r\n");
            return;
        }

        if (strcmp(argument, "0") == 0)
        {
            g_parametricTestRunning = 0U;
            g_suppressPromptOnce = 1U;
            return;
        }

        UartPrint("usage: parametric_test 0|1\r\n");
        return;
    }

    if (strncmp(line, "parametric_closed_loop", 22) == 0 &&
        (line[22] == '\0' || line[22] == ' ' || line[22] == '\t'))
    {
        char *argument = line + 22;
        while (*argument == ' ' || *argument == '\t')
        {
            argument++;
        }

        if (strcmp(argument, "1") == 0)
        {
            uint32_t now = HAL_GetTick();
            if (g_homed == 0U)
            {
                UartPrint("closed loop rejected: run home first\r\n");
                return;
            }

            g_sineRunning = 0U;
            g_parametricRunning = 0U;
            g_parametricTestRunning = 0U;
            g_run = 0U;
            g_liveTelemetry = 0U;
            g_recording = 0U;
            ClosedLoopActuatorStop();
            g_parametricClosedLoopFilterValid = 0U;
            g_parametricClosedLoopDcEstimate = 0.0f;
            g_parametricClosedLoopPrevDcBlocked = 0.0f;
            g_parametricClosedLoopQuadFiltered = 0.0f;
            g_parametricClosedLoopDirectAgcLogGain = 0.0f;
            g_parametricClosedLoopQuadAgcLogGain = 0.0f;
            g_parametricClosedLoopRateLimitedRad = 0.0f;
            g_parametricClosedLoopSampleCount = 0U;
            g_parametricClosedLoopAngleFailureCount = 0U;
            g_parametricClosedLoopStartMs = now;
            g_parametricClosedLoopNextSampleMs = now + PARAMETRIC_TEST_PERIOD_MS;
            g_parametricClosedLoopTargetSteps = g_positionSteps;
            g_closedLoopFault = 0U;
            g_closedLoopFaultPending = 0U;
            g_closedLoopKickCount = 0U;
            g_closedLoopTimerArmCount = 0U;
            g_tim6IrqCount = 0U;
            g_closedLoopActuatorCallbackCount = 0U;
            g_closedLoopStepHighCount = 0U;
            g_closedLoopStepCompleteCount = 0U;
            g_parametricClosedLoopRunning = 1U;
            g_suppressPromptOnce = 1U;
            UartPrint("time_s,theta_deg,controller_mm,motor_target_mm,actual_mm,target_steps,actual_steps,step_error\r\n");
            return;
        }

        if (strcmp(argument, "0") == 0)
        {
            g_parametricClosedLoopRunning = 0U;
            ClosedLoopActuatorStop();
            g_suppressPromptOnce = 1U;
            return;
        }

        UartPrint("usage: parametric_closed_loop 0|1\r\n");
        return;
    }

    if (strncmp(line, "scale ", 6) == 0)
    {
        float stepsPerMm = strtof(line + 6, NULL);
        if (stepsPerMm <= 0.0f || stepsPerMm > 100000.0f)
        {
            UartPrint("usage: scale <steps/mm> (0..100000)\r\n");
            return;
        }
        g_stepsPerMm = stepsPerMm;
        UartPrint("position scale updated\r\n");
        return;
    }

    if (strcmp(line, "switches") == 0)
    {
        PrintSwitches();
        return;
    }

    if (strncmp(line, "jog ", 4) == 0)
    {
        char *end;
        uint32_t dir = strtoul(line + 4, &end, 10);
        uint32_t steps = strtoul(end, NULL, 10);

        if (dir > 1U || steps == 0U || steps > MAX_HOMING_STEPS)
        {
            UartPrint("usage: jog <dir 0|1> <steps 1..20000>\r\n");
            return;
        }
        Jog((uint8_t)dir, steps);
        return;
    }

    if (strcmp(line, "home") == 0)
    {
        Home();
        return;
    }

    if (strncmp(line, "sine", 4) == 0 &&
        (line[4] == '\0' || line[4] == ' ' || line[4] == '\t'))
    {
        char *arguments = line + 4;
        while (*arguments == ' ' || *arguments == '\t')
        {
            arguments++;
        }

        char *endAmp;
        char *endFreq;
        float amplitudeMm;
        float frequency;

        if (strcmp(arguments, "stop") == 0)
        {
            g_parametricClosedLoopRunning = 0U;
            ClosedLoopActuatorStop();
            g_sineRunning = 0U;
            g_run = 0U;
            if (g_homed != 0U)
            {
                ReturnToCenter();
            }
            else
            {
                UartPrint("sine stopped\r\n");
            }
            return;
        }

        // TEMPORARY UART/PARSER DEBUG
        amplitudeMm = strtof(arguments, &endAmp);
        frequency = strtof(endAmp, &endFreq);
        {
            char debugMsg[160];
            snprintf(debugMsg, sizeof(debugMsg),
                     "SINE DEBUG amp=%.3f freq=%.3f remAmp='%s' remFreq='%s'\r\n",
                     (double)amplitudeMm,
                     (double)frequency,
                     endAmp,
                     endFreq);
            UartPrint(debugMsg);
        }

        if (g_homed == 0U)
        {
            UartPrint("sine rejected: run home first\r\n");
            return;
        }
        if (amplitudeMm <= 0.0f || amplitudeMm >= ((float)g_travelSteps / (2.0f * g_stepsPerMm)))
        {
            UartPrint("sine rejected: amplitude must be >0 and < half the travel\r\n");
            return;
        }
        if (frequency <= 0.0f || frequency > MAX_SINE_FREQUENCY_HZ)
        {
            UartPrint("sine rejected: frequency must be >0 and <=10 Hz\r\n");
            return;
        }

        g_sineAmplitudeMm = amplitudeMm;
        g_sineFrequencyHz = frequency;
        g_parametricClosedLoopRunning = 0U;
        ClosedLoopActuatorStop();
        g_parametricTestRunning = 0U;
        g_positionSteps = 0;
        g_sineStartMs = HAL_GetTick();
        g_sineRunning = 1U;
        g_parametricRunning = 0U;
        g_run = 0U;
        UartPrint("sine started\r\n");
        return;
    }

    if (strncmp(line, "parametric", 10) == 0 &&
        (line[10] == '\0' || line[10] == ' ' || line[10] == '\t'))
    {
        char *arguments = line + 10;
        while (*arguments == ' ' || *arguments == '\t')
        {
            arguments++;
        }

        char *end;
        float amplitudeMm;
        float phiDeg;

        if (strcmp(arguments, "stop") == 0)
        {
            g_parametricClosedLoopRunning = 0U;
            ClosedLoopActuatorStop();
            g_parametricRunning = 0U;
            g_run = 0U;
            if (g_homed != 0U)
            {
                ReturnToCenter();
            }
            else
            {
                UartPrint("parametric stopped\r\n");
            }
            return;
        }

        amplitudeMm = strtof(arguments, &end);
        phiDeg = strtof(end, NULL);

        if (g_homed == 0U)
        {
            UartPrint("parametric rejected: run home first\r\n");
            return;
        }
        if (amplitudeMm <= 0.0f || (amplitudeMm * 2.0f * g_stepsPerMm) >= (float)g_travelSteps)
        {
            UartPrint("parametric rejected: amplitude must be less than half the travel\r\n");
            return;
        }

        g_parametricAmplitudeMm = amplitudeMm;
        g_parametricPhiRad = phiDeg * PI_F / 180.0f;
        g_parametricClosedLoopRunning = 0U;
        ClosedLoopActuatorStop();
        g_parametricTestRunning = 0U;
        g_parametricFilterValid = 0U;
        g_parametricLastUpdateMs = 0U;
        g_parametricThetaSqDc = 0.0f;
        g_parametricPrevAcSignal = 0.0f;
        g_parametricCosPeak = 0.0f;
        g_parametricSinPeak = 0.0f;
        g_parametricCommandedMm = (float)g_positionSteps / g_stepsPerMm;
        g_parametricRunning = 1U;
        g_sineRunning = 0U;
        g_run = 0U;
        UartPrint("parametric started\r\n");
        return;
    }

    if (strncmp(line, "run ", 4) == 0)
    {
        value = strtoul(line + 4, NULL, 10);
        if (value != 0U && g_homed == 0U)
        {
            UartPrint("run rejected: run home first\r\n");
            return;
        }
        g_parametricClosedLoopRunning = 0U;
        ClosedLoopActuatorStop();
        g_parametricTestRunning = 0U;
        g_parametricRunning = 0U;
        g_sineRunning = 0U;
        g_run = (value != 0U) ? 1U : 0U;
        UartPrint(g_run ? "run enabled\r\n" : "run disabled\r\n");
        return;
    }

    if (strncmp(line, "dir ", 4) == 0)
    {
        value = strtoul(line + 4, NULL, 10);
        g_parametricClosedLoopRunning = 0U;
        ClosedLoopActuatorStop();
        SetDirection((value != 0U) ? 1U : 0U);
        UartPrint("direction updated\r\n");
        return;
    }

    if (strncmp(line, "hz ", 3) == 0)
    {
        value = strtoul(line + 3, NULL, 10);
        if (value < 20U || value > 8000U)
        {
            UartPrint("hz out of range (20..8000)\r\n");
            return;
        }
        g_targetHz = value;
        UartPrint("target speed updated\r\n");
        return;
    }

    if (strncmp(line, "accel ", 6) == 0)
    {
        value = strtoul(line + 6, NULL, 10);
        if (value < 20U || value > 50000U)
        {
            UartPrint("accel out of range (20..50000)\r\n");
            return;
        }
        g_accelHzPerSec = value;
        UartPrint("acceleration updated\r\n");
        return;
    }

    UartPrint("unknown command: [");
    UartPrint(line);
    UartPrint("]\r\n");
}

static void PollUart(void)
{
    uint8_t ch;

    if (g_uartRxOverflow != 0U)
    {
        char msg[80];
        snprintf(msg, sizeof(msg), "uart rx overflow (%lu)\r\n",
                 (unsigned long)g_uartRxOverflowCount);
        UartPrint(msg);
        g_uartRxOverflow = 0U;
    }

    while (g_uartRxTail != g_uartRxHead)
    {
        ch = g_uartRxRing[g_uartRxTail];
        g_uartRxTail = (uint16_t)((g_uartRxTail + 1U) % UART_RX_RING_SIZE);

        if (ch == '\r' || ch == '\n')
        {
            if (ch == '\n' && g_lastRxWasCr != 0U)
            {
                g_lastRxWasCr = 0U;
                continue;
            }

            g_lastRxWasCr = (ch == '\r') ? 1U : 0U;

            if (g_rxLineTooLong != 0U)
            {
                UartPrint("command too long\r\n");
                g_rxIdx = 0U;
                g_rxLineTooLong = 0U;
                if (g_suppressPromptOnce != 0U)
                {
                    g_suppressPromptOnce = 0U;
                }
                else if (g_recording == 0U)
                {
                    UartPrint("> ");
                }
                continue;
            }

            if (g_parametricTestRunning == 0U && g_parametricClosedLoopRunning == 0U)
            {
                UartPrint("\r\n");
            }
            g_rxBuf[g_rxIdx] = '\0';

            if (g_rxIdx > 0U)
            {
                ProcessLine(g_rxBuf);
            }

            g_rxIdx = 0U;
            if (g_suppressPromptOnce != 0U)
            {
                g_suppressPromptOnce = 0U;
            }
            else if (g_recording == 0U && g_parametricTestRunning == 0U &&
                     g_parametricClosedLoopRunning == 0U)
            {
                UartPrint("> ");
            }
            continue;
        }

        if (g_rxLineTooLong != 0U)
        {
            continue;
        }

        if (g_rxIdx >= (UART_RX_BUF_SIZE - 1U))
        {
            g_rxLineTooLong = 1U;
            g_rxIdx = UART_RX_BUF_SIZE - 1U;
            continue;
        }

        g_lastRxWasCr = 0U;
        g_rxBuf[g_rxIdx++] = (char)ch;
        if (g_recording == 0U && g_parametricTestRunning == 0U &&
            g_parametricClosedLoopRunning == 0U)
        {
            char echo[2] = {(char)ch, '\0'};
            UartPrint(echo);
        }
    }
}

static void GPIO_Init(void)
{
    __HAL_RCC_GPIOA_CLK_ENABLE();
    __HAL_RCC_GPIOB_CLK_ENABLE();

    GPIO_InitTypeDef GPIO_InitStruct = {0};

    GPIO_InitStruct.Pin = STEP_PIN | DIR_PIN;
    GPIO_InitStruct.Mode = GPIO_MODE_OUTPUT_PP;
    GPIO_InitStruct.Pull = GPIO_NOPULL;
    GPIO_InitStruct.Speed = GPIO_SPEED_FREQ_HIGH;
    HAL_GPIO_Init(GPIOB, &GPIO_InitStruct);

    GPIO_InitStruct.Pin = LED_PIN;
    GPIO_InitStruct.Mode = GPIO_MODE_OUTPUT_PP;
    GPIO_InitStruct.Pull = GPIO_NOPULL;
    GPIO_InitStruct.Speed = GPIO_SPEED_FREQ_LOW;
    HAL_GPIO_Init(GPIOA, &GPIO_InitStruct);

    GPIO_InitStruct.Pin = UPPER_LIMIT_PIN | LOWER_LIMIT_PIN;
    GPIO_InitStruct.Mode = GPIO_MODE_INPUT;
    GPIO_InitStruct.Pull = GPIO_PULLUP;
    GPIO_InitStruct.Speed = GPIO_SPEED_FREQ_LOW;
    HAL_GPIO_Init(GPIOA, &GPIO_InitStruct);

    // USART2 pins: PA2 (TX), PA3 (RX)
    GPIO_InitStruct.Pin = GPIO_PIN_2 | GPIO_PIN_3;
    GPIO_InitStruct.Mode = GPIO_MODE_AF_PP;
    GPIO_InitStruct.Pull = GPIO_NOPULL;
    GPIO_InitStruct.Speed = GPIO_SPEED_FREQ_LOW;
    GPIO_InitStruct.Alternate = GPIO_AF7_USART2;
    HAL_GPIO_Init(GPIOA, &GPIO_InitStruct);

    // I2C1: D14 = PB9 (SDA), D15 = PB8 (SCL).
    GPIO_InitStruct.Pin = GPIO_PIN_8 | GPIO_PIN_9;
    GPIO_InitStruct.Mode = GPIO_MODE_AF_OD;
    GPIO_InitStruct.Pull = GPIO_PULLUP;
    GPIO_InitStruct.Speed = GPIO_SPEED_FREQ_HIGH;
    GPIO_InitStruct.Alternate = GPIO_AF4_I2C1;
    HAL_GPIO_Init(GPIOB, &GPIO_InitStruct);
}

static void I2C1_Init(void)
{
    __HAL_RCC_I2C1_CLK_ENABLE();

    hi2c1.Instance = I2C1;
    hi2c1.Init.Timing = 0x00303D5BU;
    hi2c1.Init.OwnAddress1 = 0U;
    hi2c1.Init.AddressingMode = I2C_ADDRESSINGMODE_7BIT;
    hi2c1.Init.DualAddressMode = I2C_DUALADDRESS_DISABLE;
    hi2c1.Init.OwnAddress2 = 0U;
    hi2c1.Init.OwnAddress2Masks = I2C_OA2_NOMASK;
    hi2c1.Init.GeneralCallMode = I2C_GENERALCALL_DISABLE;
    hi2c1.Init.NoStretchMode = I2C_NOSTRETCH_DISABLE;

    g_i2cReady = (HAL_I2C_Init(&hi2c1) == HAL_OK) ? 1U : 0U;
}

static void USART2_Init(void)
{
    __HAL_RCC_USART2_CLK_ENABLE();
    __HAL_RCC_DMAMUX1_CLK_ENABLE();
    __HAL_RCC_DMA1_CLK_ENABLE();

    HAL_NVIC_SetPriority(USART2_IRQn, 0U, 0U);
    HAL_NVIC_EnableIRQ(USART2_IRQn);
    HAL_NVIC_SetPriority(DMA1_Channel6_IRQn, 0U, 0U);
    HAL_NVIC_EnableIRQ(DMA1_Channel6_IRQn);

    huart2.Instance = USART2;
    huart2.Init.BaudRate = UART_BAUDRATE;
    huart2.Init.WordLength = UART_WORDLENGTH_8B;
    huart2.Init.StopBits = UART_STOPBITS_1;
    huart2.Init.Parity = UART_PARITY_NONE;
    huart2.Init.Mode = UART_MODE_TX_RX;
    huart2.Init.HwFlowCtl = UART_HWCONTROL_NONE;
    huart2.Init.OverSampling = UART_OVERSAMPLING_16;
    huart2.Init.OneBitSampling = UART_ONE_BIT_SAMPLE_DISABLE;
    huart2.AdvancedInit.AdvFeatureInit = UART_ADVFEATURE_NO_INIT;

    if (HAL_UART_Init(&huart2) != HAL_OK)
    {
        Error_Handler();
    }

    hdma_usart2_rx.Instance = DMA1_Channel6;
    hdma_usart2_rx.Init.Request = DMA_REQUEST_USART2_RX;
    hdma_usart2_rx.Init.Direction = DMA_PERIPH_TO_MEMORY;
    hdma_usart2_rx.Init.PeriphInc = DMA_PINC_DISABLE;
    hdma_usart2_rx.Init.MemInc = DMA_MINC_ENABLE;
    hdma_usart2_rx.Init.PeriphDataAlignment = DMA_PDATAALIGN_BYTE;
    hdma_usart2_rx.Init.MemDataAlignment = DMA_MDATAALIGN_BYTE;
    hdma_usart2_rx.Init.Mode = DMA_NORMAL;
    hdma_usart2_rx.Init.Priority = DMA_PRIORITY_LOW;

    if (HAL_DMA_Init(&hdma_usart2_rx) != HAL_OK)
    {
        Error_Handler();
    }

    __HAL_LINKDMA(&huart2, hdmarx, hdma_usart2_rx);

    if (HAL_UARTEx_ReceiveToIdle_DMA(&huart2, g_uartDmaRxBuf, sizeof(g_uartDmaRxBuf)) != HAL_OK)
    {
        Error_Handler();
    }
    __HAL_DMA_DISABLE_IT(huart2.hdmarx, DMA_IT_HT);
}

static void Error_Handler(void)
{
    __disable_irq();
    while (1)
    {
    }
}