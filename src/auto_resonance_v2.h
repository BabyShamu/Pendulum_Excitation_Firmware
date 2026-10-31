#ifndef AUTO_RESONANCE_V2_H
#define AUTO_RESONANCE_V2_H

/* Auto-Resonance Controller V2: model-free Fourier excitation.
 * Independent from legacy controllers. Controller math is in SI units.
 * This module never accesses hardware, timers or the serial protocol.
 * The caller must enforce homing, angle/limit faults and travel bounds.
 *
 * Important: the DC blocker below approximates the Simulink sixth-order
 * DC blocker; the discrete quadrature coefficient is from the 5-ms
 * experimental firmware. Closed-loop response must be validated before
 * using large strokes. The PLL is intentionally NOT used: the original
 * quadrature phase source outperformed it in the 20-s Simscape test.
 */
#include <math.h>
#include <stdint.h>
#include <string.h>

#define ARV2_SAMPLE_S                (0.005f)
#define ARV2_DC_TAU_S                (2.0f)
#define ARV2_MIN_QUADRATURE_MAG      (0.0001f)
#define ARV2_AGC_GAIN_MAX_LOG        (6.9077553f)
#define ARV2_AGC_STEP                (0.0005f)
#define ARV2_SLEW_MM_S               (250.0f)
#define ARV2_PI                      (3.14159265358979323846f)

typedef struct
{
    float dc_estimate;
    float previous_ac;
    float quadrature;
    float direct_gain_log;
    float quadrature_gain_log;
    float command_mm;
    float measured_phase_rad;
    float waveform;
    uint8_t initialized;
    uint8_t signal_valid;
} ARV2_State;

static inline void ARV2_Reset(ARV2_State *s)
{
    memset(s,0,sizeof(*s));
}

static inline float ARV2_Limit(float x,float lo,float hi)
{
    return x < lo ? lo : (x > hi ? hi : x);
}

static inline float ARV2_Agc(float input,float *log_gain)
{
    float gain = expf(*log_gain);
    float output = input*gain;
    float power = output*output;
    if (power < 1.0e-20f) power = 1.0e-20f;
    *log_gain = ARV2_Limit(*log_gain - ARV2_AGC_STEP*logf(power),
                          -ARV2_AGC_GAIN_MAX_LOG,ARV2_AGC_GAIN_MAX_LOG);
    return output;
}

/* Returns 1 when usable angle/phase data exists. The zero command is
 * produced on initialisation and below the signal-confidence threshold.
 * Fourier mode: 1=sine, 2=two terms, 3=three terms.
 * phase_offset_rad is a commanded tuning parameter, not pendulum length.
 */
static inline uint8_t ARV2_Step(ARV2_State *s, float theta_rad,
             float amplitude_mm, float phase_offset_rad, uint8_t mode,
             float *target_mm)
{
    float theta2,ac,sine_quadrature,cosine_quadrature,mag;
    float alpha,h1,h3,h5,wave,desired,step;
    if (!isfinite(theta_rad) || !isfinite(amplitude_mm) ||
        !isfinite(phase_offset_rad) || mode < 1U || mode > 3U ||
        amplitude_mm <= 0.0f)
    {
        s->signal_valid=0U;
        s->command_mm=0.0f;
        *target_mm=0.0f;
        return 0U;
    }
    theta2=theta_rad*theta_rad;
    if (s->initialized == 0U)
    {
        s->dc_estimate=theta2;
        s->previous_ac=0.0f;
        s->quadrature=0.0f;
        s->initialized=1U;
        s->signal_valid=0U;
        *target_mm=0.0f;
        return 0U;
    }
    s->dc_estimate += ARV2_SAMPLE_S/(ARV2_SAMPLE_S+ARV2_DC_TAU_S)*
                      (theta2-s->dc_estimate);
    ac=theta2-s->dc_estimate;
    s->quadrature=0.9048f*s->quadrature+
                  20.0f*(ac-s->previous_ac);
    s->previous_ac=ac;

    mag=hypotf(ac,s->quadrature);
    s->signal_valid=(isfinite(mag) &&
                     mag>=ARV2_MIN_QUADRATURE_MAG) ? 1U : 0U;
    if (s->signal_valid == 0U)
    {
        desired=0.0f;
        s->waveform=0.0f;
    }
    else
    {
        /* Quadrature AGC for parity with the original AR estimator.
         * atan2 uses normalized ratios; gain states affect orientation.
         */
        sine_quadrature=ARV2_Agc(s->quadrature,&s->quadrature_gain_log);
        cosine_quadrature=ARV2_Agc(ac,&s->direct_gain_log);
        alpha=atan2f(sine_quadrature,-cosine_quadrature)+phase_offset_rad;
        s->measured_phase_rad=alpha;
        h1=sinf(alpha);
        h3=sinf(3.0f*alpha);
        h5=sinf(5.0f*alpha);
        if (mode==1U) wave=h1;
        else if (mode==2U) wave=1.15470053838f*(h1+h3/6.0f);
        else wave=1.20713f*h1+0.28101f*h3+0.07389f*h5;
        s->waveform=wave;
        desired=amplitude_mm*ARV2_Limit(wave,-1.0f,1.0f);
    }
    step=ARV2_SLEW_MM_S*ARV2_SAMPLE_S;
    s->command_mm+=ARV2_Limit(desired-s->command_mm,-step,step);
    *target_mm=s->command_mm;
    return s->signal_valid;
}
#endif
