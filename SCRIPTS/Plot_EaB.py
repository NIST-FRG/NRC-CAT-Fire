
import pandas as pd
import matplotlib.pyplot as plt

def create_aging_plot(file_path):
    # Load the data starting from the second row (header=0 assumes first row is header)
    df = pd.read_csv('../DATA/EaB/' + file_path + '.csv')

    # Temperatures to plot based on column suffixes
    temperatures = ['155', '135', '115', '90']
    
    # Create the figure with a square size to help maintain 1:1 aspect ratio
    fig, ax = plt.subplots(figsize=(4, 4))

    for temp in temperatures:
        x_col = f'Days-{temp}'
        y_col = f'EaB-{temp}'
        
        if x_col in df.columns and y_col in df.columns:
            # Extract columns and remove NaN values
            data_subset = df[[x_col, y_col]].dropna()
            
            # Plot using log-log scale
            ax.plot(data_subset[x_col], 
                    data_subset[y_col], 
                    marker='o', 
                    linestyle='-', 
                    label=f'{temp} C')

    # Label the axes
    ax.set_xscale('log')
    ax.set_xlabel('Aging Time (days)')
    ax.set_ylabel('Elongation (%)')

    # Set axis limits
    # Note: On a log scale, the minimum cannot be 0. 
    # Using 0.1 for X (as requested) and a small positive value for Y.
    ax.set_xlim(0.1, 200)
    ax.set_ylim(1, 500)
    ax.set_box_aspect(1)

    # Add grid and legend
   #ax.grid(True, which="both", linestyle='--', alpha=0.5)
    ax.legend()
    ax.text(0.2,460, file_path, fontsize=12)

    plt.tight_layout()
    fig.savefig('../SCRIPT_FIGURES/' + file_path + '.pdf', format='pdf')

if __name__ == "__main__":
    # Ensure the file "EaB_Results.csv" exists in the local directory
    create_aging_plot('EaB_Results_TP_Jacket')
    create_aging_plot('EaB_Results_TS_Jacket')
    create_aging_plot('EaB_Results_TP_Insulation')
    create_aging_plot('EaB_Results_TS_Insulation')

