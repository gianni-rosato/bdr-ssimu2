import argparse
import json
import os
import subprocess
import vapoursynth as vs
from vapoursynth import core
import matplotlib.pyplot as plt
from tqdm import tqdm

def encode_video(source, output, ffmpeg_command, crf):
    command = ffmpeg_command.format(input=source, output=output, crf=crf)
    subprocess.run(command, shell=True, check=True)

# Only calculate the average & harmonic mean for progress bar
def calc_some_scores(score_list: list[int]):
    average: float = sum(score_list)/len(score_list)
    list_of_reciprocals = [1/score for score in score_list if score != 0] # Omits scores that are 0
    harmonic_mean: float = len(score_list)/sum(list_of_reciprocals)
    return (average, harmonic_mean)

def calculate_metrics(source_path, encoded_path, every: int):
    core = vs.core

    source_clip = core.ffms2.Source(source=source_path)
    encoded_clip = core.ffms2.Source(source=encoded_path)

    source_clip = source_clip.resize.Bicubic(format=vs.RGBS, matrix_in_s='709')
    encoded_clip = encoded_clip.resize.Bicubic(format=vs.RGBS, matrix_in_s='709')

    if (every > 1):
        source_clip = source_clip.std.SelectEvery(cycle=every, offsets=0)
        encoded_clip = encoded_clip.std.SelectEvery(cycle=every, offsets=0)

    source_clip = source_clip.fmtc.transfer(transs="srgb", transd="linear", bits=32)
    encoded_clip = encoded_clip.fmtc.transfer(transs="srgb", transd="linear", bits=32)

    result = source_clip.ssimulacra2.SSIMULACRA2(encoded_clip)

    ssim_scores = []
    with tqdm(total=result.num_frames, desc="Calculating SSIMULACRA2 scores", unit=" frame") as pbar:
        for frame in result.frames():
            ssim_scores.append(frame.props['_SSIMULACRA2'])
            pbar.update(1)
            if (frame % 24 == 0):
                (average, harmonic_mean) = calc_some_scores(ssim_scores)
                pbar.set_postfix({
                    'avg': f"{average:.2f}",
                    'harmean': f"{harmonic_mean:.2f}"
                })

    average = sum(ssim_scores) / len(ssim_scores)
    harmonic_mean = len(ssim_scores) / sum([1.0/s for s in ssim_scores if s != 0])
    return (average, harmonic_mean)

def plot_results(data, output_file, mode, codec_1, codec_2):
    plt.figure(figsize=(10, 6))
    for label, points in data.items():
        bpp = [p['bitrate'] for p in points]
        ssimu2 = [p['ssimu2'] for p in points]
        plt.plot(bpp, ssimu2, marker='o', linestyle='-', label=label)

        for i, (x, y) in enumerate(zip(bpp, ssimu2)):
            plt.annotate(f'CRF{points[i]["crf"]}', (x, y), textcoords="offset points", xytext=(0,10), ha='center')

    if (f"{mode}" == "mean"):
        mode_pretty = "Average"
    else:
        mode_pretty = "Harmonic Mean"

    plt.xlabel('Bitrate (kb/s)')
    plt.ylabel(f'{mode_pretty} SSIMULACRA2 Score')
    plt.title(f'BD-Rate Curve: {codec_1} vs {codec_2} (SSIMULACRA2)')
    plt.legend()
    plt.grid(True)
    plt.savefig(output_file, format="webp")
    plt.close()

def process_results(codec, ffmpeg_command, crf_start, crf_end, crf_step, every, source):
    codec_results = []
    codec_results_harmonic = []
    for crf in range(crf_start, crf_end + 1, crf_step):
        output_file = f"encoded_{codec}_crf{crf}.mp4"

        print(f"Encoding {codec} with CRF {crf}")
        encode_video(source, output_file, ffmpeg_command, crf)

        print(f"Calculating metrics for {codec} with CRF {crf}")
        ssimu2_scores = calculate_metrics(source, output_file, every)
        ssimu2_mean = ssimu2_scores[0]
        ssimu2_harmonic = ssimu2_scores[1]

        file_size = os.path.getsize(output_file)
        # Bitrate only works on MP4s and MOVs
        bitrate = float(subprocess.check_output(['ffprobe', '-v', 'quiet', '-select_streams', 'v:0', '-show_entries', 'stream=bit_rate', '-of', 'default=noprint_wrappers=1:nokey=1', output_file]).strip())
        bitrate_k: float = bitrate / 1000

        codec_results.append({
            'crf': crf,
            'ssimu2': ssimu2_mean,
            'bitrate': bitrate_k
        })

        codec_results_harmonic.append({
            'crf': crf,
            'ssimu2': ssimu2_harmonic,
            'bitrate': bitrate_k
        })

        os.remove(output_file) # Clean up encoded file

    return (codec_results, codec_results_harmonic)


def main():
    parser = argparse.ArgumentParser(description='Encode, analyze, and plot SSIMULACRA2 scores using a source video file that is encoded with FFmpeg.')
    parser.add_argument('source', help='Source video path')
    parser.add_argument('-cs1', '--crf_start_1', type=int, default=15, help='Starting CRF value (first codec). Default 15')
    parser.add_argument('-ce1', '--crf_end_1', type=int, default=35, help='Ending CRF value (first codec). Default 30')
    parser.add_argument('-ct1', '--crf_step_1', type=int, default=5, help='CRF step size (first codec). Default 5')
    parser.add_argument('-cs2', '--crf_start_2', type=int, default=15, help='Starting CRF value (second codec). Default 15')
    parser.add_argument('-ce2', '--crf_end_2', type=int, default=35, help='Ending CRF value (second codec). Default 30')
    parser.add_argument('-ct2', '--crf_step_2', type=int, default=5, help='CRF step size (second codec). Default 5')
    parser.add_argument('-e', '--every', type=int, default=1, help='Only score every nth frame. Default 1 (every frame)')
    parser.add_argument('-t', '--threads', dest='threads', type=int, default=0, help='Number of threads. Default 0 (auto)')
    args = parser.parse_args()

    # User-specified codec strings & settings go here
    codec_1 = 'libx264'
    codec_2 = 'libx265'
    ffmpeg_commands = {
        f'{codec_1}': 'ffmpeg -y -hide_banner -loglevel error -i {input} -c:v libx264 -crf {crf} -preset fast {output}',
        f'{codec_2}': 'ffmpeg -y -hide_banner -loglevel error -i {input} -c:v libx265 -crf {crf} -preset fast {output}'
    }

    results = {}
    results_harmonic = {}

    # Process results
    results_1 = process_results(f'{codec_1}', ffmpeg_commands[f'{codec_1}'], args.crf_start_1, args.crf_end_1, args.crf_step_1, args.every, args.source)
    results_2 = process_results(f'{codec_2}', ffmpeg_commands[f'{codec_2}'], args.crf_start_2, args.crf_end_2, args.crf_step_2, args.every, args.source)

    # Assign results
    results[f'{codec_1}'] = results_1[0]
    results[f'{codec_2}'] = results_2[0]
    results_harmonic[f'{codec_1}'] = results_1[1]
    results_harmonic[f'{codec_2}'] = results_2[1]

    # Plot results
    plot_results(results, f"curve-{codec_1}_vs_{codec_2}_every-{int(args.every)}-mean.webp", "mean", codec_1, codec_2)
    plot_results(results_harmonic, f"curve-{codec_1}_vs_{codec_2}_every-{int(args.every)}-harmean.webp", "harmean", codec_1, codec_2)

    # Save results to JSON for future reference
    with open(f"results-{codec_1}_vs_{codec_2}_every-{int(args.every)}.json", "w") as f:
        json.dump(results, f, indent=2)

if __name__ == "__main__":
    main()
