import numpy as np
import cv2
import imageio
import os

# === Paths ===
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
INPUT_DIR = os.path.join(SCRIPT_DIR, "../input")
OUTPUT_DIR = os.path.join(SCRIPT_DIR, "../output")
DEBUG_DIR = os.path.join(SCRIPT_DIR, "../debug")
os.makedirs(OUTPUT_DIR, exist_ok=True)
os.makedirs(DEBUG_DIR, exist_ok=True)

# === Settings ===
N_FRAMES = 30
ANIMATE_FREQUENCY = 0.15
GABOR_THRESHOLD = 0.22
MAX_DISPLACEMENT = 15.0

# Define orientations for stroke detection (in radians)
ORIENTATIONS = [np.pi/6, np.pi/3, np.pi/2, 2*np.pi/3, 5*np.pi/6]

def create_gabor_kernel(ksize, sigma, theta, lambd, gamma, psi):
    """
    Create a Gabor kernel with the given parameters
    
    Parameters:
    - ksize: Size of the kernel (width, height)
    - sigma: Standard deviation of the Gaussian envelope
    - theta: Orientation of the Gabor filter in radians
    - lambd: Wavelength of the sinusoidal factor
    - gamma: Spatial aspect ratio
    - psi: Phase offset of the sinusoidal factor
    
    Returns:
    - Gabor kernel
    """
    # Convert size to sigma for Gabor function
    sigma_x = sigma
    sigma_y = sigma / gamma
    
    # Calculate half size
    xmax = ksize[0] // 2
    ymax = ksize[1] // 2
    
    # Create grid
    y, x = np.mgrid[-ymax:ymax+1, -xmax:xmax+1]
    
    # Rotation
    xprime = x * np.cos(theta) + y * np.sin(theta)
    yprime = -x * np.sin(theta) + y * np.cos(theta)
    
    # Calculate Gabor kernel
    gaussian = np.exp(-(xprime**2 / (2 * sigma_x**2) + yprime**2 / (2 * sigma_y**2)))
    sinusoid = np.cos(2 * np.pi * xprime / lambd + psi)
    
    kernel = gaussian * sinusoid
    
    # Normalize kernel
    kernel = kernel - np.mean(kernel)
    kernel = kernel / np.sqrt(np.sum(kernel**2)) if np.sum(kernel**2) > 0 else kernel
    
    return kernel

def apply_gabor_filter(image, kernel):
    """
    Apply Gabor filter to an image
    
    Parameters:
    - image: Input image
    - kernel: Gabor kernel
    
    Returns:
    - Filtered image
    """
    # Apply filter using convolution
    filtered = cv2.filter2D(image, cv2.CV_32F, kernel)
    
    # Get the absolute value (magnitude of response)
    magnitude = np.abs(filtered)
    
    # Normalize the output
    magnitude = magnitude / magnitude.max() if magnitude.max() > 0 else magnitude
    
    return magnitude

def gabor_responses(gray):
    """Apply Gabor filter for different orientations to extract stroke features"""
    responses = []
    
    # Gabor parameters
    ksize = (31, 31)  # Filter size
    sigma = 5.0       # Standard deviation of the Gaussian envelope
    lambd = 1.0 / ANIMATE_FREQUENCY  # Wavelength of the sinusoidal factor
    gamma = 0.5       # Spatial aspect ratio
    psi = 0           # Phase offset
    
    for theta in ORIENTATIONS:
        # Create Gabor kernel
        kernel = create_gabor_kernel(ksize, sigma, theta, lambd, gamma, psi)
        
        # Apply filter and get magnitude
        magnitude = apply_gabor_filter(gray, kernel)
        
        # Save kernel for debugging
        kernel_vis = (kernel - np.min(kernel)) / (np.max(kernel) - np.min(kernel)) * 255
        cv2.imwrite(os.path.join(DEBUG_DIR, f"gabor_kernel_theta_{theta:.2f}.png"), 
                   kernel_vis.astype(np.uint8))
        
        responses.append((magnitude, theta))
    
    return responses

def create_angle_specific_masks(gray):
    """Create masks for brushstrokes at different angles"""
    angle_masks = []
    
    # Get Gabor responses for different orientations
    gabor_results = gabor_responses(gray)
    
    # Process each orientation
    for magnitude, theta in gabor_results:
        # Create binary mask for the stroke area
        mask = (magnitude > GABOR_THRESHOLD).astype(np.float32)
        
        # Clean the mask with morphological operations
        kernel = np.ones((3, 3), np.uint8)
        mask = cv2.morphologyEx(mask.astype(np.uint8), cv2.MORPH_OPEN, kernel).astype(np.float32)
        mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel).astype(np.float32)
        
        # Only add masks that have actual content
        if np.sum(mask) > 100:  # Minimum area threshold
            # Save the mask for debugging
            debug_mask = (mask * 255).astype(np.uint8)
            cv2.imwrite(os.path.join(DEBUG_DIR, f"mask_theta_{theta:.2f}.png"), debug_mask)
            
            # Store mask with its direction
            angle_masks.append((mask, theta))
    
    return angle_masks

def create_direction_vector(theta):
    """Create a movement direction vector for a given angle"""
    # The direction vector is perpendicular to the stroke direction
    # This makes the flow move along the brushstrokes
    dx = -np.sin(theta)  # perpendicular direction x
    dy = np.cos(theta)   # perpendicular direction y
    
    # Normalize vector
    mag = np.sqrt(dx*dx + dy*dy)
    if mag > 0:
        dx /= mag
        dy /= mag
        
    return dx, dy

def apply_shape_preserving_animation(image, angle_masks, t):
    """
    Apply animation to the original image while preserving the shape of brushstroke areas.
    This works by:
    1. Creating a displacement field for each orientation
    2. Applying the displacement to a copy of the original image
    3. Using the ORIGINAL, UNMOVED mask to blend the animated content back into the result
    """
    h, w = image.shape[:2]
    # Start with original image
    result = np.copy(image)
    
    # Process each angle-specific mask separately
    for mask, theta in angle_masks:
        # Store the original unmoved mask - this is crucial
        original_mask = mask.copy()
        
        # Get perpendicular direction vector
        dx, dy = create_direction_vector(theta)
        
        # Create a linear oscillating movement pattern
        cycle = 2 * (t % 1.0)
        if cycle > 1.0:
            cycle = 2.0 - cycle  # Create triangular wave pattern (0->1->0)
        
        # Calculate displacement amount
        displacement = (cycle - 0.5) * 2 * MAX_DISPLACEMENT
        
        # Create displacement field - but only apply it to the IMAGE, not to the mask
        y_grid, x_grid = np.mgrid[0:h, 0:w].astype(np.float32)
        map_x = x_grid + original_mask * dx * displacement
        map_y = y_grid + original_mask * dy * displacement
        
        # Remap the ENTIRE original image using this displacement field
        animated_content = cv2.remap(image, map_x, map_y, 
                                   interpolation=cv2.INTER_LINEAR, 
                                   borderMode=cv2.BORDER_REFLECT)
        
        # Use the ORIGINAL, UNMOVED mask to blend the animated content back
        # This is the key step that preserves the brushstroke shapes
        original_mask_3d = np.dstack([original_mask] * 3)
        result = np.where(original_mask_3d > 0, animated_content, result)
        
        # Create debug visualization (only for first frame)
        if t < 0.1:
            flow_vis = np.zeros((h, w, 3), dtype=np.uint8)
            flow_vis[:,:,1] = (original_mask * 255).astype(np.uint8)  # Show mask in green
            
            # Show movement direction with arrows
            for y in range(0, h, 25):
                for x in range(0, w, 25):
                    if original_mask[y, x] > 0.5:
                        end_x = int(x + dx * 20)
                        end_y = int(y + dy * 20)
                        cv2.arrowedLine(flow_vis, (x, y), (end_x, end_y), (0, 0, 255), 1)
            
            cv2.imwrite(os.path.join(DEBUG_DIR, f"flow_direction_theta_{theta:.2f}.png"), flow_vis)
    
    return result

def enhance_brushstrokes(gray):
    """Process to enhance the visibility of brushstrokes in the image"""
    clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
    enhanced = clahe.apply((gray * 255).astype(np.uint8))
    
    # Edge enhancement
    edge_kernel = np.array([[-1, -1, -1], 
                           [-1,  9, -1], 
                           [-1, -1, -1]])
    enhanced = cv2.filter2D(enhanced, -1, edge_kernel)
    
    return enhanced / 255.0

def process_image(image_path, output_dir, debug_dir):
    """Process a single image to create an animated GIF"""
    print(f"🎨 Processing {os.path.basename(image_path)}")
    
    # Read and prepare image
    image = cv2.imread(image_path)
    if image is None:
        print(f"⚠️ Could not read {os.path.basename(image_path)}")
        return
    
    # Enhanced preprocessing
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY) / 255.0
    enhanced_gray = enhance_brushstrokes(gray)
    
    # Create masks for different brushstroke directions
    angle_masks = create_angle_specific_masks(enhanced_gray)
    
    # Visualize combined mask for debugging
    combined = np.zeros_like(gray)
    for mask, _ in angle_masks:
        combined = np.maximum(combined, mask)
    cv2.imwrite(os.path.join(debug_dir, "combined_stroke_mask.png"), 
               (combined * 255).astype(np.uint8))
    
    # Generate frames for animation
    frames = []
    for i in range(N_FRAMES):
        t = i / N_FRAMES
        # Apply shape-preserving animation
        animated_frame = apply_shape_preserving_animation(image, angle_masks, t)
        
        # Convert to RGB for GIF
        rgb_frame = cv2.cvtColor(animated_frame, cv2.COLOR_BGR2RGB)
        frames.append(rgb_frame)
        
        # Save intermediate frame for debugging
        if debug_dir and i % 5 == 0:
            cv2.imwrite(os.path.join(debug_dir, f"frame_{i:03d}.png"), animated_frame)

    # Save the animated GIF
    base_name = os.path.splitext(os.path.basename(image_path))[0]
    gif_path = os.path.join(output_dir, f"animated_{base_name}.gif")
    imageio.mimsave(gif_path, frames, duration=0.07)
    print(f"✅ Saved animation to {gif_path}")
    
    return gif_path

def main():
    """Process all images in the input directory"""
    # Process all compatible images in the input directory
    for filename in os.listdir(INPUT_DIR):
        if filename.lower().endswith((".png", ".jpg", ".jpeg")):
            image_path = os.path.join(INPUT_DIR, filename)
            process_image(image_path, OUTPUT_DIR, DEBUG_DIR)

if __name__ == "__main__":
    main()