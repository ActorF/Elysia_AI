/**
 * Mount the bounded Live2D renderer behind a declarative React surface.
 * The controller remains imperative so animation frames do not cause React
 * renders, while semantic state and emotion still flow through typed props.
 */

import { useEffect, useLayoutEffect, useRef } from 'react'

import {
  createLive2DController,
  type Live2DFraming,
} from './live2d-runtime.ts'
import { bindLive2DRuntimeFailure } from './live2d-runtime-failure.ts'
import type { CharacterEmotion } from './character-emotion.ts'
import type { CharacterState } from './character-state.ts'

interface Live2DCharacterCanvasProps {
  readonly emotion: CharacterEmotion
  readonly framing: Exclude<Live2DFraming, 'full-body'>
  readonly onStatusChange: (status: 'loading' | 'ready' | 'failed') => void
  readonly state: CharacterState
}

type Live2DController = Awaited<ReturnType<typeof createLive2DController>>

/**
 * Own one Live2D controller for the lifetime of its canvas.
 *
 * Creation failures stay inside the visual boundary and are reported through
 * `onStatusChange`; CharacterArtwork can therefore retain its reviewed static
 * fallback without allowing optional animation to affect Chat or Voice.
 */
export function Live2DCharacterCanvas({
  emotion,
  framing,
  onStatusChange,
  state,
}: Live2DCharacterCanvasProps) {
  const canvasRef = useRef<HTMLCanvasElement | null>(null)
  const controllerRef = useRef<Live2DController | null>(null)
  const emotionRef = useRef(emotion)
  const stateRef = useRef(state)
  const statusCallbackRef = useRef(onStatusChange)

  useEffect(() => {
    emotionRef.current = emotion
    stateRef.current = state
    statusCallbackRef.current = onStatusChange
  }, [emotion, onStatusChange, state])

  useLayoutEffect(() => {
    // A fresh canvas has no controller even if its parent remembers that the
    // preceding Animated-mode mount was ready. Reset before browser paint so
    // the reviewed fallback covers asynchronous reinitialization.
    statusCallbackRef.current('loading')
  }, [])

  useEffect(() => {
    const canvas = canvasRef.current
    if (canvas === null) {
      statusCallbackRef.current('failed')
      return undefined
    }

    let active = true
    let ownedController: Live2DController | null = null
    // Initialization errors reject the creation promise, while failures after
    // readiness arrive through this canvas event so React can restore the same
    // reviewed fallback without coupling to WebGL or PurismCore internals.
    const unbindRuntimeFailure = bindLive2DRuntimeFailure(
      canvas,
      () => {
        const failedController = ownedController
        ownedController = null
        if (controllerRef.current === failedController) {
          controllerRef.current = null
        }
        return failedController
      },
      () => {
        active = false
        statusCallbackRef.current('failed')
      },
    )
    void createLive2DController(canvas, {
      emotion: emotionRef.current,
      framing,
      state: stateRef.current,
    }).then((controller) => {
      if (!active) {
        controller.dispose()
        return
      }
      ownedController = controller
      controllerRef.current = controller
      controller.setState(stateRef.current)
      controller.setEmotion(emotionRef.current)
      statusCallbackRef.current('ready')
    }).catch(() => {
      // Model, WebGL, CSP, or asset failures are deliberately indistinguishable
      // here: every case degrades to the same safe reviewed static artwork.
      if (active) {
        statusCallbackRef.current('failed')
      }
    })

    return () => {
      active = false
      unbindRuntimeFailure()
      if (controllerRef.current === ownedController) {
        controllerRef.current = null
      }
      ownedController?.dispose()
    }
  }, [framing])

  useEffect(() => {
    controllerRef.current?.setState(state)
  }, [state])

  useEffect(() => {
    controllerRef.current?.setEmotion(emotion)
  }, [emotion])

  return (
    <canvas
      ref={canvasRef}
      className="character-artwork-live2d"
      data-live2d-framing={framing}
      role="img"
      aria-label="Animated Elysia character"
    />
  )
}
