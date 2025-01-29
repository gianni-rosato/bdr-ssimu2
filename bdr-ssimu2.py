#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.13"
# dependencies = [
#     "matplotlib>=3.10.0",
#     "tqdm>=4.67.1",
#     "vapoursynth>=70",
# ]
# ///


import argparse
import json
import os
import subprocess

import matplotlib.pyplot as plt
import vapoursynth as vs
from tqdm import tqdm


def encode_video(source, output, ffmpeg_command, crf):
    """
    Encode a video using FFmpeg with a specific CRF value.
    """
    command = ffmpeg_command.format(input=source, output=output, crf=crf)
    subprocess.run(command, shell=True, check=True)


# Only calculate the average & harmonic mean for progress bar
def calc_some_scores(score_list: list[float]):
    """
    Calculate the average, harmonic mean, standard deviation, & 10th percentile of a list of scores.
    """
    average: float = sum(score_list) / len(score_list)
    # Filter out zeros and negative numbers for harmonic mean
    positive_scores: list[float] = [score for score in score_list if score > 0.0]
    negative_scores: list[float] = [score for score in score_list if score <= 0.0]
    if positive_scores:
        list_of_pos_reciprocals: list[float] = [1 / score for score in positive_scores]
        list_of_neg_reciprocals: list[float] = [1 / score for score in negative_scores]
        sum_reciprocals: float = sum(list_of_pos_reciprocals) - sum(
            list_of_neg_reciprocals
        )
        harmonic_mean: float = len(positive_scores) / sum_reciprocals
    else:
        harmonic_mean: float = 0.0  # or float('nan') or None, depending on requirements
    std_dev: float = (
        sum((x - average) ** 2 for x in score_list) / len(score_list)
    ) ** 0.5
    percentile_10th: float = sorted(score_list)[int(len(score_list) * 0.1)]
    return (average, harmonic_mean, std_dev, percentile_10th)


def calculate_metrics(source_path, encoded_path, every: int, metric, threads: int):
    """
    Calculate SSIMULACRA2 or XPSNR scores between a source video and an encoded video.
    """
    core = vs.core

    source_clip = core.ffms2.Source(
        source=source_path, cache=False, threads=int(threads if "threads" != 0 else -1)
    )
    encoded_clip = core.ffms2.Source(
        source=encoded_path, cache=False, threads=int(threads if "threads" != 0 else -1)
    )

    source_clip = source_clip.resize.Bicubic(format=vs.RGBS, matrix_in_s="709")
    encoded_clip = encoded_clip.resize.Bicubic(format=vs.RGBS, matrix_in_s="709")

    if every > 1:
        source_clip = source_clip.std.SelectEvery(cycle=every, offsets=0)
        encoded_clip = encoded_clip.std.SelectEvery(cycle=every, offsets=0)

    if metric == "ssimu2":
        result = source_clip.vszip.Metrics(encoded_clip, [0])
    elif metric == "xpsnr":
        result = source_clip.vszip.Metrics(encoded_clip, [1])
    else:
        raise ValueError(f"Invalid metric: {metric}")

    scores: list[float] = []
    with tqdm(
        total=result.num_frames, desc=f"Calculating {metric} scores", unit=" frame"
    ) as pbar:
        for idx, frame in enumerate(result.frames()):
            if metric == "ssimu2":
                scores.append(frame.props["_SSIMULACRA2"])
            else:
                scores.append(frame.props["_XPSNR"])

            pbar.update(1)
            if idx % 24 == 0:
                (average, harmonic_mean, std_dev, percentile_10th) = calc_some_scores(
                    scores
                )
                pbar.set_postfix(
                    {
                        "avg": f"{average:.2f}",
                        "harmean": f"{harmonic_mean:.2f}",
                        "std": f"{std_dev:.2f}",
                        "p10": f"{percentile_10th:.2f}",
                    }
                )

    return calc_some_scores(scores)


def plot_results(
    data,
    output_file,
    mode,
    codec_1,
    codec_2,
    codec1_str,
    codec2_str,
    format,
    input_filename,
):
    """
    Plot SSIMULACRA2 or XPSNR scores between two codecs visually.
    """
    plt.figure(figsize=(10, 6))
    plt.style.use("dark_background")

    for label, points in data.items():
        bpp = [p["bitrate"] for p in points]
        ssimu2 = [p["ssimu2"] for p in points]
        plt.plot(bpp, ssimu2, marker="o", linestyle="-.", label=label)

        for i, (x, y) in enumerate(zip(bpp, ssimu2)):
            plt.annotate(
                f'CRF{points[i]["crf"]}',
                (x, y),
                textcoords="offset points",
                xytext=(0, 10),
                ha="center",
            )

    if f"{mode}" == "mean":
        mode_pretty = "Average"
    elif f"{mode}" == "harmean":
        mode_pretty = "Harmonic Mean"
    elif f"{mode}" == "stddev":
        mode_pretty = "Standard Deviation"
    else:
        mode_pretty = "10th Percentile"

    # Set grid color to grey
    plt.grid(True, color="grey", linestyle="--", linewidth=0.5, alpha=0.5)

    # Set axes color to grey
    plt.gca().spines["bottom"].set_color("grey")
    plt.gca().spines["left"].set_color("grey")
    plt.gca().spines["top"].set_color("grey")
    plt.gca().spines["right"].set_color("grey")

    plt.xlabel("Bitrate (kb/s)", color="gainsboro", family="monospace")
    plt.ylabel(
        f"{mode_pretty} SSIMULACRA2 Score", color="gainsboro", family="monospace"
    )

    # Main title
    plt.title(
        f"{input_filename}: {codec_1} vs {codec_2} (SSIMULACRA2)",
        color="white",
        family="monospace",
        pad=12,
    )

    plt.legend(edgecolor="grey", facecolor="black")
    plt.tick_params(axis="both", colors="grey")

    plt.tight_layout()
    plt.savefig(output_file, format=f"{format}", dpi=200)
    plt.close()


def process_results(
    codec, ffmpeg_command, crf_start, crf_end, crf_step, every, source, threads
):
    """
    Run the encoding and metric calculation process for a codec.
    """
    codec_results_ssimu2 = []
    codec_results_harmonic_ssimu2 = []
    codec_results_stddev_ssimu2 = []
    codec_results_percentile10_ssimu2 = []
    codec_results_xpsnr = []
    codec_results_harmonic_xpsnr = []
    for crf in range(crf_start, crf_end + 1, crf_step):
        output_file = f"encoded_{codec}_crf{crf}.mp4"

        print(f"Encoding {codec} with CRF {crf}")
        encode_video(source, output_file, ffmpeg_command, crf)

        print(f"Calculating metrics for {codec} with CRF {crf}")
        ssimu2_scores = calculate_metrics(source, output_file, every, "ssimu2", threads)
        ssimu2_mean = ssimu2_scores[0]
        ssimu2_harmonic = ssimu2_scores[1]
        ssimu2_stddev = ssimu2_scores[2]
        ssimu2_percentile10 = ssimu2_scores[3]

        # Uncomment when vszip has XPSNR support
        # xpsnr_scores = calculate_metrics(source, output_file, 1, "xpsnr", threads)
        # xpsnr_mean = xpsnr_scores[0]
        # xpsnr_harmonic = xpsnr_scores[1]

        file_size = os.path.getsize(output_file)
        # Bitrate only works on MP4s and MOVs
        bitrate = float(
            subprocess.check_output(
                [
                    "ffprobe",
                    "-v",
                    "quiet",
                    "-select_streams",
                    "v:0",
                    "-show_entries",
                    "stream=bit_rate",
                    "-of",
                    "default=noprint_wrappers=1:nokey=1",
                    output_file,
                ]
            ).strip()
        )
        bitrate_k: float = bitrate / 1000

        codec_results_ssimu2.append(
            {"crf": crf, "ssimu2": ssimu2_mean, "bitrate": bitrate_k}
        )

        codec_results_harmonic_ssimu2.append(
            {"crf": crf, "ssimu2": ssimu2_harmonic, "bitrate": bitrate_k}
        )

        codec_results_stddev_ssimu2.append(
            {"crf": crf, "ssimu2": ssimu2_stddev, "bitrate": bitrate_k}
        )

        codec_results_percentile10_ssimu2.append(
            {"crf": crf, "ssimu2": ssimu2_percentile10, "bitrate": bitrate_k}
        )

        # Uncomment when vszip has XPSNR support
        # codec_results_xpsnr.append({
        #     'crf': crf,
        #     'xpsnr': xpsnr_mean,
        #     'bitrate': bitrate_k
        # })
        #
        # codec_results_harmonic_xpsnr.append({
        #     'crf': crf,
        #     'xpsnr': xpsnr_harmonic,
        #     'bitrate': bitrate_k
        # })

        os.remove(output_file)  # Clean up encoded file
    # Uncomment when vszip has XPSNR support
    # return (codec_results_ssimu2, codec_results_harmonic_ssimu2, codec_results_xpsnr, codec_results_harmonic_xpsnr)
    return (
        codec_results_ssimu2,
        codec_results_harmonic_ssimu2,
        codec_results_stddev_ssimu2,
        codec_results_percentile10_ssimu2,
    )


def main():
    parser = argparse.ArgumentParser(
        description="Encode, analyze, and plot SSIMULACRA2 scores using a source video file that is encoded with FFmpeg."
    )
    parser.add_argument("source", help="Source video path")
    parser.add_argument(
        "-cs1",
        "--crf_start_1",
        type=int,
        default=15,
        help="Starting CRF value (first codec). Default 15",
    )
    parser.add_argument(
        "-ce1",
        "--crf_end_1",
        type=int,
        default=35,
        help="Ending CRF value (first codec). Default 30",
    )
    parser.add_argument(
        "-ct1",
        "--crf_step_1",
        type=int,
        default=5,
        help="CRF step size (first codec). Default 5",
    )
    parser.add_argument(
        "-cs2",
        "--crf_start_2",
        type=int,
        default=15,
        help="Starting CRF value (second codec). Default 15",
    )
    parser.add_argument(
        "-ce2",
        "--crf_end_2",
        type=int,
        default=35,
        help="Ending CRF value (second codec). Default 30",
    )
    parser.add_argument(
        "-ct2",
        "--crf_step_2",
        type=int,
        default=5,
        help="CRF step size (second codec). Default 5",
    )
    parser.add_argument(
        "-e",
        "--every",
        type=int,
        default=1,
        help="Only score every nth frame. Default 1 (every frame)",
    )
    parser.add_argument(
        "-t",
        "--threads",
        dest="threads",
        type=int,
        default=0,
        help="Number of threads. Default 0 (auto)",
    )
    parser.add_argument(
        "-f",
        "--format",
        type=str,
        default="svg",
        help="Save to format webp, png, svg. Default svg",
    )
    args = parser.parse_args()

    input_filename: str = os.path.splitext(args.source)[0]
    input_filename_nopath: str = os.path.splitext(os.path.basename(args.source))[0]
    format: str = args.format
    try:
        os.mkdir("plots")
    except FileExistsError:
        print('Error: "plots" directory already exists. Please clean up manually')
        exit(1)

    try:
        os.mkdir("json_logs")
    except FileExistsError:
        print('Error: "json_logs" directory already exists. Please clean up manually')
        exit(1)

    # User-specified codec strings & settings go here
    codec_1 = "x264"
    codec_2 = "x265"
    ffmpeg_commands = {
        f"{codec_1}": "ffmpeg -y -hide_banner -loglevel error -i {input} -pix_fmt yuv420p -c:v libx264 -preset ultrafast -crf {crf} -profile:v high {output}",
        f"{codec_2}": "ffmpeg -y -hide_banner -loglevel error -i {input} -pix_fmt yuv420p -c:v libx265 -preset ultrafast -crf {crf} -profile:v main {output}",
    }

    results_ssimu2 = {}
    results_harmonic_ssimu2 = {}
    results_stddev_ssimu2 = {}
    results_percentile10_ssimu2 = {}
    # results_xpsnr = {}
    # results_harmonic_xpsnr = {}

    # Process results
    results_1_all = process_results(
        f"{codec_1}",
        ffmpeg_commands[f"{codec_1}"],
        args.crf_start_1,
        args.crf_end_1,
        args.crf_step_1,
        args.every,
        args.source,
        args.threads,
    )
    results_2_all = process_results(
        f"{codec_2}",
        ffmpeg_commands[f"{codec_2}"],
        args.crf_start_2,
        args.crf_end_2,
        args.crf_step_2,
        args.every,
        args.source,
        args.threads,
    )

    # Assign results
    results_ssimu2[f"{codec_1}"] = results_1_all[0]
    results_ssimu2[f"{codec_2}"] = results_2_all[0]
    results_harmonic_ssimu2[f"{codec_1}"] = results_1_all[1]
    results_harmonic_ssimu2[f"{codec_2}"] = results_2_all[1]
    results_stddev_ssimu2[f"{codec_1}"] = results_1_all[2]
    results_stddev_ssimu2[f"{codec_2}"] = results_2_all[2]
    results_percentile10_ssimu2[f"{codec_1}"] = results_1_all[3]
    results_percentile10_ssimu2[f"{codec_2}"] = results_2_all[3]

    # Uncomment when vszip has XPSNR support
    # results_xpsnr[f'{codec_1}'] = results_1_all[2]
    # results_xpsnr[f'{codec_2}'] = results_2_all[2]
    # results_harmonic_xpsnr[f'{codec_1}'] = results_1_all[3]
    # results_harmonic_xpsnr[f'{codec_2}'] = results_2_all[3]

    # Plot results
    plot_results(
        results_ssimu2,
        f"plots/{input_filename_nopath}_curve-{codec_1}_vs_{codec_2}_every-{int(args.every)}-ssimu2-mean.{format}",
        "mean",
        codec_1,
        codec_2,
        ffmpeg_commands[f"{codec_1}"],
        ffmpeg_commands[f"{codec_2}"],
        format,
        input_filename,
    )
    plot_results(
        results_harmonic_ssimu2,
        f"plots/{input_filename_nopath}_curve-{codec_1}_vs_{codec_2}_every-{int(args.every)}-ssimu2-harmean.{format}",
        "harmean",
        codec_1,
        codec_2,
        ffmpeg_commands[f"{codec_1}"],
        ffmpeg_commands[f"{codec_2}"],
        format,
        input_filename,
    )
    plot_results(
        results_stddev_ssimu2,
        f"plots/{input_filename_nopath}_curve-{codec_1}_vs_{codec_2}_every-{int(args.every)}-ssimu2-stddev.{format}",
        "stddev",
        codec_1,
        codec_2,
        ffmpeg_commands[f"{codec_1}"],
        ffmpeg_commands[f"{codec_2}"],
        format,
        input_filename,
    )
    plot_results(
        results_percentile10_ssimu2,
        f"plots/{input_filename_nopath}_curve-{codec_1}_vs_{codec_2}_every-{int(args.every)}-ssimu2-p10.{format}",
        "p10",
        codec_1,
        codec_2,
        ffmpeg_commands[f"{codec_1}"],
        ffmpeg_commands[f"{codec_2}"],
        format,
        input_filename,
    )

    # Uncomment when vszip has XPSNR support
    # plot_results(results_xpsnr, f"plots/{input_filename_nopath}_curve-{codec_1}_vs_{codec_2}_every-{int(args.every)}-xpsnr-mean.{format}", "mean", codec_1, codec_2, ffmpeg_commands[f'{codec_1}'], ffmpeg_commands[f'{codec_2}'], format, input_filename)
    # plot_results(results_harmonic_xpsnr, f"plots/{input_filename_nopath}_curve-{codec_1}_vs_{codec_2}_every-{int(args.every)}-xpsnr-harmean.{format}", "harmean", codec_1, codec_2, ffmpeg_commands[f'{codec_1}'], ffmpeg_commands[f'{codec_2}'], format, input_filename)

    # Save results to JSON for future reference
    with open(
        f"json_logs/{input_filename_nopath}_results-{codec_1}_vs_{codec_2}_every-{int(args.every)}-ssimu2.json",
        "w",
    ) as f:
        json.dump(results_ssimu2, f, indent=2)

    with open(
        f"json_logs/{input_filename_nopath}_results-{codec_1}_vs_{codec_2}_every-{int(args.every)}-ssimu2-harmean.json",
        "w",
    ) as f:
        json.dump(results_harmonic_ssimu2, f, indent=2)

    with open(
        f"json_logs/{input_filename_nopath}_results-{codec_1}_vs_{codec_2}_every-{int(args.every)}-ssimu2-stddev.json",
        "w",
    ) as f:
        json.dump(results_stddev_ssimu2, f, indent=2)

    with open(
        f"json_logs/{input_filename_nopath}_results-{codec_1}_vs_{codec_2}_every-{int(args.every)}-ssimu2-p10.json",
        "w",
    ) as f:
        json.dump(results_percentile10_ssimu2, f, indent=2)

    # Uncomment when vszip has XPSNR support
    # with open(f"json_logs/{input_filename_nopath}_results-{codec_1}_vs_{codec_2}_every-{int(args.every)}-xpsnr.json", "w") as f:
    #     json.dump(results_xpsnr, f, indent=2)
    #
    # with open(f"json_logs/{input_filename_nopath}_results-{codec_1}_vs_{codec_2}_every-{int(args.every)}-xpsnr-harmean.json", "w") as f:
    #     json.dump(results_harmonic_xpsnr, f, indent=2)

    with open(
        f"json_logs/{input_filename_nopath}_commands-{codec_1}_vs_{codec_2}_every-{int(args.every)}-ssimu2.json",
        "w",
    ) as f:
        json.dump(ffmpeg_commands, f, indent=2)


if __name__ == "__main__":
    main()
